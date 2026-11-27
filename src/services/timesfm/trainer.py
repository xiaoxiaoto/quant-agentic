"""TimesFM 多因子训练服务：分布特征提取、可选主干微调、Purged Walk-Forward 验证与融合头校准。"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal
from uuid import uuid4

import lightgbm as lgb
import numpy as np
from scipy.special import expit
from scipy.stats import spearmanr

from src.agents.timesfm import TimesFMAgent
from src.agents.timesfm_multifactor import TimesFMMultiFactorAgent
from src.contracts.domains.timesfm_multifactor import (
    TimesFMDatasetManifest,
    TimesFMExperimentRecord,
    TimesFMFinetuneSummary,
)
from src.contracts.enums.trading import HoldingPeriod
from src.contracts.exceptions import TimesFMDatasetError, TimesFMTrainingError
from src.contracts.schemas.model import WalkForwardFoldReport
from src.contracts.schemas.timesfm_multifactor import TimesFMTrainReport, TimesFMTrainRequest
from src.repositories.timesfm_experiment import TimesFMExperimentRepository
from src.services.timesfm.dataset_builder import TimesFMDatasetBuilder

_Pool = dict[str, np.ndarray]


class TimesFMTrainer:
    """串联 TimesFM 条件预测特征、融合头训练、独立校准、滚动样本外验证与暂存产物落盘。"""

    _TRAIN_FRACTION: ClassVar[float] = 0.75
    _MIN_POOL: ClassVar[int] = 80
    _FINETUNE_MAX_STEPS: ClassVar[int] = 64
    _FINETUNE_MAX_SAMPLES: ClassVar[int] = 2048
    _CHECKPOINT_DIR: ClassVar[str] = "timesfm_checkpoint"

    def __init__(
        self,
        builder: TimesFMDatasetBuilder,
        timesfm_agent: TimesFMAgent,
        experiment_repo: TimesFMExperimentRepository,
        experiment_dir: Path,
        *,
        agent_factory: Callable[[str | None], TimesFMAgent] | None = None,
    ) -> None:
        self._builder = builder
        self._agent = timesfm_agent
        self._repo = experiment_repo
        self._experiment_dir = experiment_dir
        self._agent_factory = agent_factory

    def _create_isolated_agent(self, checkpoint_path: str | None = None) -> TimesFMAgent:
        """构造与线上共享单例完全隔离的离线 TimesFMAgent 实例。"""
        if self._agent_factory is not None:
            return self._agent_factory(checkpoint_path)
        return self._agent.clone_isolated(checkpoint_path=checkpoint_path)

    @staticmethod
    def _utc(epoch: float) -> datetime:
        return datetime.fromtimestamp(float(epoch), UTC)

    @staticmethod
    def _train_lgb_pair(
        train_x: np.ndarray, train_y: np.ndarray, feature_names: list[str], rounds: int
    ) -> tuple[lgb.Booster, lgb.Booster]:
        params = {
            "verbosity": -1,
            "num_threads": 2,
            "seed": 42,
            "deterministic": True,
            "force_col_wise": True,
            "num_leaves": 7,
            "min_data_in_leaf": 5,
            "learning_rate": 0.05,
        }
        regressor = lgb.train(
            {**params, "objective": "regression"},
            lgb.Dataset(train_x, label=train_y, feature_name=feature_names),
            num_boost_round=rounds,
        )
        classifier = lgb.train(
            {**params, "objective": "binary"},
            lgb.Dataset(train_x, label=(train_y > 0).astype(int), feature_name=feature_names),
            num_boost_round=rounds,
        )
        return regressor, classifier

    @staticmethod
    def _fit_ridge(train_x: np.ndarray, train_y: np.ndarray, alpha: float = 1.0) -> dict[str, Any]:
        valid = np.isfinite(train_x)
        counts = np.sum(valid, axis=0)
        sums = np.sum(np.where(valid, train_x, 0.0), axis=0)
        means = np.where(counts > 0, sums / np.maximum(counts, 1), 0.0)
        filled = np.where(valid, train_x, means)
        stds = np.std(filled, axis=0)
        safe = np.where(stds > 1e-12, stds, 1.0)
        norm = (filled - means) / safe
        design = np.column_stack([norm, np.ones(len(norm))])
        reg = np.eye(design.shape[1]) * alpha
        reg[-1, -1] = 0.0
        solution = np.linalg.solve(design.T @ design + reg, design.T @ train_y)
        return {
            "weights": [float(v) for v in solution[:-1]],
            "intercept": float(solution[-1]),
            "feature_means": [float(v) for v in means],
            "feature_stds": [float(v) for v in safe],
        }

    @staticmethod
    def _predict_entry(
        entry: dict[str, Any], head: Literal["lgb", "ridge"], features: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """返回融合头在特征矩阵上的预测净收益与概率校准用原始分数。"""
        if head == "lgb":
            regressor = lgb.Booster(model_str=entry["regressor"])
            classifier = lgb.Booster(model_str=entry["classifier"])
            return (
                np.asarray(regressor.predict(features, num_threads=2), dtype=np.float64),
                np.asarray(classifier.predict(features, raw_score=True, num_threads=2), dtype=np.float64),
            )
        means = np.asarray(entry["feature_means"], dtype=np.float64)
        stds = np.asarray(entry["feature_stds"], dtype=np.float64)
        filled = np.where(np.isfinite(features), features, means)
        pred = ((filled - means) / stds) @ np.asarray(entry["weights"], dtype=np.float64) + float(entry["intercept"])
        return pred, pred

    @staticmethod
    def _correlations(pred: np.ndarray, actual: np.ndarray) -> tuple[float | None, float | None]:
        if len(pred) < 3 or float(np.std(pred)) < 1e-15 or float(np.std(actual)) < 1e-15:
            return None, None
        ic = float(np.corrcoef(pred, actual)[0, 1])
        rank = float(getattr(spearmanr(pred, actual), "statistic", float("nan")))
        return (ic if math.isfinite(ic) else None, rank if math.isfinite(rank) else None)

    def _fit_window(
        self,
        pool: _Pool,
        *,
        head: Literal["lgb", "ridge"],
        feature_names: list[str],
        rounds: int,
        confidence: float,
    ) -> dict[str, Any]:
        """在时间有序样本上按 75%/25% 清洗切分训练融合头并独立校准。"""
        count = len(pool["y"])
        split = int(count * self._TRAIN_FRACTION)
        if count - split < 20:
            raise ValueError("每个持有期至少需要 80 条有效样本（其中 20 条用于独立时间校准）")
        cal_start = pool["as_of"][split]
        train_keep = pool["label_end"][:split] < cal_start
        if int(train_keep.sum()) < 40:
            raise ValueError("剔除跨分割点标签后有效训练样本不足 40 条")
        train_x, train_y = pool["x"][:split][train_keep], pool["y"][:split][train_keep]
        cal_x, cal_y = pool["x"][split:], pool["y"][split:]
        if len(set((train_y > 0).tolist())) < 2 or len(set((cal_y > 0).tolist())) < 2:
            raise ValueError("训练集与校准集必须各自包含盈利与亏损样本")
        if head == "lgb":
            regressor, classifier = self._train_lgb_pair(train_x, train_y, feature_names, rounds)
            cal_pred = np.asarray(regressor.predict(cal_x, num_threads=2), dtype=np.float64)
            cal_scores = np.asarray(classifier.predict(cal_x, raw_score=True, num_threads=2), dtype=np.float64)
            extra: dict[str, Any] = {
                "regressor": regressor.model_to_string(),
                "classifier": classifier.model_to_string(),
            }
        else:
            extra = self._fit_ridge(train_x, train_y)
            cal_pred, cal_scores = self._predict_entry(extra, head, cal_x)
        return TimesFMMultiFactorAgent.fit_calibration_entry(
            cal_predicted=cal_pred,
            cal_raw_scores=cal_scores,
            cal_actual=cal_y,
            cal_timesfm_point=cal_x[:, 0],
            confidence_level=confidence,
            training_count=int(train_keep.sum()),
            purged_count=split - int(train_keep.sum()),
            trained_until=self._utc(float(pool["label_end"][:split][train_keep].max())),
            calibrated_until=self._utc(float(pool["label_end"][split:].max())),
            extra_payload=extra,
        )

    def _walk_forward(
        self,
        pool: _Pool,
        *,
        head: Literal["lgb", "ridge"],
        feature_names: list[str],
        n_splits: int,
        rounds: int,
        confidence: float,
        min_test_epoch: float | None = None,
    ) -> tuple[list[WalkForwardFoldReport], dict[str, float]]:
        total = len(pool["y"])
        folds: list[WalkForwardFoldReport] = []
        ics: list[float] = []
        rank_ics: list[float] = []
        if total < 90:
            return folds, {}
        min_train = max(self._MIN_POOL, int(total * 0.5))
        if min_test_epoch is not None:
            cutoff_idx = int(np.searchsorted(pool["as_of"], min_test_epoch, side="left"))
            min_train = max(min_train, cutoff_idx)
        if min_train >= total:
            return folds, {}
        fold_size = max(1, (total - min_train) // n_splits)
        for fold_idx in range(n_splits):
            test_start = min_train + fold_idx * fold_size
            test_end = total if fold_idx == n_splits - 1 else min(total, test_start + fold_size)
            if test_start >= test_end:
                break
            test_start_at = pool["as_of"][test_start]
            keep = pool["label_end"][:test_start] < test_start_at
            if int(keep.sum()) < self._MIN_POOL:
                continue
            train_pool = {key: value[:test_start][keep] for key, value in pool.items()}
            try:
                entry = self._fit_window(
                    train_pool, head=head, feature_names=feature_names, rounds=rounds, confidence=confidence
                )
            except ValueError:
                continue
            test_x, test_y = pool["x"][test_start:test_end], pool["y"][test_start:test_end]
            preds, raw = self._predict_entry(entry, head, test_x)
            probs = expit(float(entry["probability_slope"]) * raw + float(entry["probability_intercept"]))
            lcb = preds - float(entry["residual_bound"])
            actual_pos = (test_y > 0).astype(np.float64)
            ic, rank_ic = self._correlations(preds, test_y)
            if ic is not None:
                ics.append(ic)
            if rank_ic is not None:
                rank_ics.append(rank_ic)
            folds.append(
                WalkForwardFoldReport(
                    fold_index=len(folds) + 1,
                    train_count=int(entry["training_count"]),
                    test_count=len(test_y),
                    test_start_at=self._utc(float(test_start_at)),
                    test_end_at=self._utc(float(pool["as_of"][test_end - 1])),
                    mae=round(float(np.mean(np.abs(preds - test_y))), 6),
                    directional_accuracy=round(float(np.mean((preds > 0) == (test_y > 0))), 4),
                    brier_score=round(float(np.mean((probs - actual_pos) ** 2)), 6),
                    mean_predicted_return=round(float(np.mean(preds)), 6),
                    mean_actual_return=round(float(np.mean(test_y)), 6),
                    lcb_coverage=round(float(np.mean(test_y >= lcb)), 4),
                )
            )
        summary: dict[str, float] = {}
        if folds:
            summary = {
                "valid_folds": float(len(folds)),
                "avg_mae": round(statistics.mean(f.mae for f in folds), 6),
                "avg_directional_accuracy": round(statistics.mean(f.directional_accuracy for f in folds), 4),
                "avg_brier_score": round(statistics.mean(f.brier_score for f in folds), 6),
                "avg_lcb_coverage": round(statistics.mean(f.lcb_coverage for f in folds), 4),
            }
            if ics:
                summary["avg_ic"] = round(statistics.mean(ics), 6)
            if rank_ics:
                summary["avg_rank_ic"] = round(statistics.mean(rank_ics), 6)
        return folds, summary

    def _finetune(
        self,
        isolated_agent: TimesFMAgent,
        arrays: dict[str, np.ndarray],
        manifest: TimesFMDatasetManifest,
        request: TimesFMTrainRequest,
        horizons: list[HoldingPeriod],
        cutoff_epoch: float,
        checkpoint_dir: Path,
    ) -> TimesFMFinetuneSummary:
        """仅在校准起点之前的窗口上构造微调样本，并委托隔离 TimesFMAgent 执行微调与保存。"""
        times = arrays["times"]
        steps = min(min(manifest.horizon_steps[h.value] for h in horizons), self._FINETUNE_MAX_STEPS)
        last_end = int(np.searchsorted(times, cutoff_epoch, side="left")) - 1
        t_max = last_end - steps
        context = max(32, (manifest.context_length // 32) * 32)
        while context > 32 and t_max - (context - 1) < 8:
            context -= 32
        if t_max - (context - 1) < 8:
            raise TimesFMTrainingError("校准起点之前的可用窗口不足，无法微调")
        candidates = np.arange(context - 1, t_max + 1)
        if len(candidates) > self._FINETUNE_MAX_SAMPLES:
            candidates = candidates[np.linspace(0, len(candidates) - 1, self._FINETUNE_MAX_SAMPLES).astype(int)]
        local_manifest = manifest.model_copy(update={"context_length": context})

        contexts: list[np.ndarray] = []
        targets: list[np.ndarray] = []
        past_only: list[np.ndarray] = []
        past_future: list[np.ndarray] = []
        for index in candidates:
            ctx, po, pf = self._builder.build_window(
                arrays, local_manifest, int(index), steps, use_covariates=request.use_covariates
            )
            contexts.append(ctx)
            targets.append(arrays["bid"][int(index) + 1 : int(index) + 1 + steps])
            if po is not None and pf is not None:
                past_only.append(po)
                past_future.append(pf)

        return isolated_agent.finetune_backbone(
            contexts=contexts,
            targets=targets,
            steps=steps,
            checkpoint_dir=checkpoint_dir,
            past_only_covariates=past_only or None,
            past_future_covariates=past_future or None,
            epochs=request.finetune_epochs,
            learning_rate=request.finetune_lr,
            batch_size=request.finetune_batch_size,
            unfreeze_last_layers=request.unfreeze_last_layers,
        )

    def _build_pool(
        self,
        arrays: dict[str, np.ndarray],
        manifest: TimesFMDatasetManifest,
        horizon: HoldingPeriod,
        holdout_epoch: float,
        max_samples: int | None,
    ) -> _Pool:
        key = horizon.value
        idx = arrays[f"idx_{key}"]
        label = arrays[f"label_{key}"]
        label_end = arrays[f"label_end_{key}"]
        keep = label_end < holdout_epoch
        idx, label, label_end = idx[keep], label[keep], label_end[keep]
        if max_samples is not None and len(idx) > max_samples:
            pick = np.linspace(0, len(idx) - 1, max_samples).astype(int)
            idx, label, label_end = idx[pick], label[pick], label_end[pick]
        if len(idx) < self._MIN_POOL:
            raise TimesFMDatasetError(f"{key} 期限训练池仅 {len(idx)} 条样本，至少需要 {self._MIN_POOL} 条")
        return {"idx": idx, "y": label.astype(np.float64), "label_end": label_end, "as_of": arrays["times"][idx]}

    def extract_distributions(
        self,
        arrays: dict[str, np.ndarray],
        manifest: TimesFMDatasetManifest,
        indices: np.ndarray,
        steps: int,
        *,
        use_covariates: bool,
        batch_size: int,
        agent: TimesFMAgent | None = None,
    ) -> np.ndarray:
        """批量调用 TimesFM 条件预测，返回 (N, 7) 收益分布特征矩阵。"""
        active_agent = agent or self._agent
        rows: list[list[float]] = []
        for start in range(0, len(indices), batch_size):
            chunk = indices[start : start + batch_size]
            contexts: list[np.ndarray] = []
            past_only: list[np.ndarray] = []
            past_future: list[np.ndarray] = []
            for index in chunk:
                ctx, po, pf = self._builder.build_window(
                    arrays, manifest, int(index), steps, use_covariates=use_covariates
                )
                contexts.append(ctx)
                if po is not None and pf is not None:
                    past_only.append(po)
                    past_future.append(pf)
            try:
                dists = active_agent.predict_multifactor_batch(
                    contexts,
                    steps,
                    past_only_covariates=past_only if use_covariates else None,
                    past_future_covariates=past_future if use_covariates else None,
                    batch_size=batch_size,
                )
            except (ValueError, RuntimeError, ImportError) as exc:
                raise TimesFMTrainingError(f"TimesFM 条件预测特征提取失败：{exc}") from exc
            rows.extend(
                [
                    d.point_return,
                    d.q10_return,
                    d.q90_return,
                    d.iqr_return,
                    d.path_mean_return,
                    d.path_min_return,
                    d.skew,
                ]
                for d in dists
            )
        return np.asarray(rows, dtype=np.float64).reshape(len(indices), 7)

    def train_and_record(self, request: TimesFMTrainRequest, *, dataset_dir: Path) -> TimesFMTrainReport:
        """执行完整训练流程，写入暂存产物并登记实验记录。"""
        manifest, arrays = self._builder.load_dataset(dataset_dir)
        horizons = list(dict.fromkeys(request.horizons or manifest.horizons))
        missing = [h.value for h in horizons if h not in manifest.horizons]
        if missing:
            raise TimesFMDatasetError(f"数据集未包含持有期：{', '.join(missing)}")
        times = arrays["times"]
        holdout_start_at: datetime | None = None
        holdout_end_at: datetime | None = None
        holdout_epoch = float("inf")
        if request.holdout_ratio > 0:
            holdout_idx = max(1, int(len(times) * (1.0 - request.holdout_ratio)))
            holdout_epoch = float(times[holdout_idx])
            holdout_start_at = self._utc(holdout_epoch)
            holdout_end_at = self._utc(float(times[-1]))

        pools = {h: self._build_pool(arrays, manifest, h, holdout_epoch, request.max_samples) for h in horizons}
        run_id = f"tfm_run_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:6]}"
        staging_dir = self._experiment_dir / "runs" / run_id
        finetune = TimesFMFinetuneSummary()
        checkpoint_relpath: str | None = None
        min_test_epoch: float | None = None
        active_agent = self._agent
        if request.train_mode == "backbone_finetune":
            cutoff = min(float(p["as_of"][int(len(p["y"]) * self._TRAIN_FRACTION)]) for p in pools.values())
            active_agent = self._create_isolated_agent()
            finetune = self._finetune(
                active_agent, arrays, manifest, request, horizons, cutoff, staging_dir / self._CHECKPOINT_DIR
            )
            checkpoint_relpath = self._CHECKPOINT_DIR
            min_test_epoch = cutoff

        feature_names = list(TimesFMMultiFactorAgent.DIST_FEATURE_NAMES) + list(manifest.tabular_factors)
        entries: dict[str, dict[str, Any]] = {}
        folds_by_horizon: dict[str, list[WalkForwardFoldReport]] = {}
        summary_by_horizon: dict[str, dict[str, float]] = {}
        for horizon, pool in pools.items():
            steps = manifest.horizon_steps[horizon.value]
            dist = self.extract_distributions(
                arrays,
                manifest,
                pool["idx"],
                steps,
                use_covariates=request.use_covariates,
                batch_size=request.batch_size,
                agent=active_agent,
            )
            pool["x"] = np.hstack([dist, arrays["tabular"][pool["idx"]]])
            folds, summary = self._walk_forward(
                pool,
                head=request.head_model_type,
                feature_names=feature_names,
                n_splits=request.n_splits,
                rounds=request.num_boost_round,
                confidence=request.confidence_level,
                min_test_epoch=min_test_epoch,
            )
            folds_by_horizon[horizon.value] = folds
            if summary:
                summary_by_horizon[horizon.value] = summary
            try:
                entry = self._fit_window(
                    pool,
                    head=request.head_model_type,
                    feature_names=feature_names,
                    rounds=request.num_boost_round,
                    confidence=request.confidence_level,
                )
            except ValueError as exc:
                raise TimesFMTrainingError(f"{horizon.value} 期限融合头训练失败：{exc}") from exc
            entry["steps"] = steps
            entries[horizon.value] = entry

        TimesFMMultiFactorAgent.save_artifact(
            staging_dir,
            head_model_type=request.head_model_type,
            features=feature_names,
            horizons=entries,
            metadata={
                "experiment_name": request.experiment_name,
                "run_id": run_id,
                "dataset_id": manifest.dataset_id,
                "product_code": manifest.product_code,
                "source_mode": manifest.source_mode,
                "freq": manifest.freq,
                "factor_preset": manifest.factor_preset,
                "context_length": manifest.context_length,
                "train_mode": request.train_mode,
                "use_covariates": request.use_covariates,
                "holdout_start_at": holdout_start_at.isoformat() if holdout_start_at else None,
            },
            finetuned_checkpoint_relpath=checkpoint_relpath,
        )
        record = TimesFMExperimentRecord(
            experiment_name=request.experiment_name,
            run_id=run_id,
            dataset_id=manifest.dataset_id,
            dataset_dir=str(dataset_dir),
            product_code=manifest.product_code,
            freq=manifest.freq,
            train_mode=request.train_mode,
            head_model_type=request.head_model_type,
            factor_preset=manifest.factor_preset,
            use_covariates=request.use_covariates,
            context_length=manifest.context_length,
            horizons=horizons,
            holdout_ratio=request.holdout_ratio,
            holdout_start_at=holdout_start_at,
            holdout_end_at=holdout_end_at,
            oos_summary=summary_by_horizon,
            finetune_summary=finetune,
            staging_dir=str(staging_dir),
            created_at=datetime.now(UTC),
        )
        self._repo.append(record)
        info = TimesFMMultiFactorAgent(staging_dir).get_model_info()
        return TimesFMTrainReport(
            experiment=record,
            sample_counts={h.value: len(p["y"]) for h, p in pools.items()},
            walk_forward_folds=folds_by_horizon,
            trained_model_info=info,
        )
