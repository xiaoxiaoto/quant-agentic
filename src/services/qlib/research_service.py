"""Qlib 离线量化研究编排服务：数据导出、表达式因子评估、滚动训练与实验管理。"""

from __future__ import annotations

import math
import operator
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import lightgbm as lgb
import numpy as np
from scipy.special import expit
from scipy.stats import spearmanr

from src.agents.qlib import QlibAgent
from src.contracts.domains.product import FeeRule
from src.contracts.domains.qlib import QlibExperimentRecord, QlibFactorMetric
from src.contracts.exceptions import QlibDatasetError
from src.contracts.schemas.model import WalkForwardFoldReport
from src.contracts.schemas.qlib import (
    QlibExportReport,
    QlibExportRequest,
    QlibFactorEvaluateReport,
    QlibFactorEvaluateRequest,
    QlibStatusResponse,
    QlibTrainReport,
    QlibTrainRequest,
)
from src.repositories.qlib_experiment import QlibExperimentRepository
from src.services.qlib.data_exporter import QlibDataExporter
from src.services.qlib.factor_library import QlibFactorLibrary
from src.services.qlib.runtime_bridge import QlibRuntimeBridge


class QlibResearchService:
    """编排 Qlib 数据导出、因子评估、Purged Walk-Forward 训练、校准导出与实验登记。"""

    def __init__(
        self,
        *,
        exporter: QlibDataExporter,
        bridge: QlibRuntimeBridge,
        provider_uri: Path,
        experiment_dir: Path,
        fee_rule: FeeRule,
        experiment_repo: QlibExperimentRepository | None = None,
        qlib_enabled: bool = False,
        predictor_backend: str = "auto",
        external_provider_uri: Path | None = None,
        active_model_dir: str | None = None,
    ) -> None:
        self._exporter = exporter
        self._bridge = bridge
        self._provider_uri = provider_uri
        self._experiment_dir = experiment_dir
        self._experiment_repo = experiment_repo or QlibExperimentRepository(experiment_dir)
        self._fee_rule = fee_rule
        self._qlib_enabled = qlib_enabled
        self._predictor_backend = predictor_backend
        self._external_provider_uri = external_provider_uri
        self._active_model_dir = active_model_dir

    def _resolve_provider_uri(
        self,
        source_type: Literal["local_sqlite", "external_qlib"],
        override_uri: str | None,
    ) -> Path:
        if override_uri is not None:
            path = Path(override_uri)
        elif source_type == "external_qlib":
            if self._external_provider_uri is None:
                raise QlibDatasetError("未配置外部 Qlib 数据源路径（QLIB_EXTERNAL_PROVIDER_URI）")
            path = self._external_provider_uri
        else:
            path = self._provider_uri
        if not path.exists():
            raise QlibDatasetError(f"Qlib 数据集目录不存在：{path}")
        return path

    def _resolve_instruments(
        self,
        provider_uri: Path,
        source_type: Literal["local_sqlite", "external_qlib"],
        requested: list[str] | None,
    ) -> list[str]:
        if requested:
            return list(dict.fromkeys(item.strip() for item in requested if item.strip()))
        manifest = self._exporter.inspect_dataset(provider_uri, source_type=source_type)
        if manifest is None or not manifest.instruments:
            raise QlibDatasetError(f"无法从数据目录 {provider_uri} 解析可用标的列表")
        return manifest.instruments

    @staticmethod
    def _coerce_utc_datetime(raw_ts: Any) -> datetime:
        if hasattr(raw_ts, "to_pydatetime"):
            dt = raw_ts.to_pydatetime()
        elif isinstance(raw_ts, datetime):
            dt = raw_ts
        else:
            dt = datetime.fromisoformat(str(raw_ts))
        return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    @staticmethod
    def _extract_matrix_rows(
        frame: Any,
        *,
        feature_count: int,
        horizon_step: int,
        freq: Literal["1h", "day"],
    ) -> list[tuple[datetime, datetime, np.ndarray, float]]:
        """将 Qlib 返回的 MultiIndex DataFrame 归一化为 (entry_dt, label_end_at, x, y) 序列。

        按 instrument 分组使用 Qlib 交易日历上向后偏移 horizon_step 后的真实时间戳作为 label_end_at，
        避免按固定自然小时清除时在周末休市或日频场景下产生前视数据泄漏。
        """
        values = np.asarray(frame.to_numpy(dtype=np.float64), dtype=np.float64)
        index = frame.index
        grouped: dict[str, list[tuple[datetime, np.ndarray, float]]] = {}
        for idx in range(len(values)):
            key = index[idx]
            if isinstance(key, tuple) and len(key) >= 2:
                inst = str(key[0])
                raw_dt = key[1]
            else:
                inst = "__single__"
                raw_dt = key
            dt = QlibResearchService._coerce_utc_datetime(raw_dt)
            row = values[idx]
            grouped.setdefault(inst, []).append((dt, row[:feature_count], float(row[feature_count])))

        fallback_bar = timedelta(days=1) if freq == "day" else timedelta(hours=1)
        rows: list[tuple[datetime, datetime, np.ndarray, float]] = []
        for inst_rows in grouped.values():
            inst_rows.sort(key=operator.itemgetter(0))
            n_inst = len(inst_rows)
            for pos, (entry_dt, feats, label) in enumerate(inst_rows):
                if not math.isfinite(label) or label <= -1.0:
                    continue
                if not np.any(np.isfinite(feats)):
                    continue
                end_pos = pos + horizon_step
                if end_pos < n_inst:
                    label_end_at = inst_rows[end_pos][0]
                else:
                    label_end_at = entry_dt + fallback_bar * horizon_step
                rows.append((entry_dt, label_end_at, feats, label))
        rows.sort(key=operator.itemgetter(0))
        return rows

    @staticmethod
    def _train_lgb_pair(
        train_x: np.ndarray,
        train_y: np.ndarray,
        feature_names: list[str],
        num_boost_round: int,
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
            num_boost_round=num_boost_round,
        )
        classifier = lgb.train(
            {**params, "objective": "binary"},
            lgb.Dataset(train_x, label=(train_y > 0).astype(int), feature_name=feature_names),
            num_boost_round=num_boost_round,
        )
        return regressor, classifier

    @staticmethod
    def _fit_ridge_model(
        train_x: np.ndarray,
        train_y: np.ndarray,
        alpha: float = 1.0,
    ) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
        means = np.nanmean(train_x, axis=0)
        means = np.where(np.isfinite(means), means, 0.0)
        filled = np.where(np.isfinite(train_x), train_x, means)
        stds = np.std(filled, axis=0)
        safe_stds = np.where(stds > 1e-12, stds, 1.0)
        norm_x = (filled - means) / safe_stds
        n_features = norm_x.shape[1]
        design = np.column_stack([norm_x, np.ones(len(norm_x), dtype=np.float64)])
        reg = np.eye(n_features + 1, dtype=np.float64) * alpha
        reg[-1, -1] = 0.0
        solution = np.linalg.solve(design.T @ design + reg, design.T @ train_y)
        return solution[:-1], float(solution[-1]), means, safe_stds

    @staticmethod
    def _predict_ridge(
        x: np.ndarray,
        weights: np.ndarray,
        intercept: float,
        means: np.ndarray,
        stds: np.ndarray,
    ) -> np.ndarray:
        filled = np.where(np.isfinite(x), x, means)
        norm_x = (filled - means) / stds
        return norm_x @ weights + intercept

    def _fit_and_calibrate_window(
        self,
        rows: list[tuple[datetime, datetime, np.ndarray, float]],
        *,
        feature_names: list[str],
        model_type: Literal["lgb", "linear"],
        num_boost_round: int,
        confidence_level: float,
    ) -> dict[str, Any]:
        split = int(len(rows) * 0.75)
        cal_rows = rows[split:]
        if len(cal_rows) < 20:
            raise ValueError("每个持有期至少需要 80 条有效样本（其中 20 条用于独立时间校准）")
        cal_start = cal_rows[0][0]
        train_rows = [row for row in rows[:split] if row[1] < cal_start]
        if len(train_rows) < 40:
            raise ValueError("剔除跨分割点标签后有效训练样本不足 40 条")

        train_x = np.vstack([row[2] for row in train_rows])
        train_y = np.asarray([row[3] for row in train_rows], dtype=np.float64)
        cal_x = np.vstack([row[2] for row in cal_rows])
        cal_y = np.asarray([row[3] for row in cal_rows], dtype=np.float64)
        if len(set((train_y > 0).tolist())) < 2 or len(set((cal_y > 0).tolist())) < 2:
            raise ValueError("训练集与校准集必须各自包含盈利与亏损样本")

        if model_type == "lgb":
            regressor, classifier = self._train_lgb_pair(train_x, train_y, feature_names, num_boost_round)
            cal_pred = np.asarray(regressor.predict(cal_x, num_threads=2), dtype=np.float64)
            cal_scores = np.asarray(classifier.predict(cal_x, raw_score=True, num_threads=2), dtype=np.float64)
            extra_payload: dict[str, Any] = {
                "regressor": regressor.model_to_string(),
                "classifier": classifier.model_to_string(),
            }
        else:
            weights, intercept, means, stds = self._fit_ridge_model(train_x, train_y)
            cal_pred = self._predict_ridge(cal_x, weights, intercept, means, stds)
            cal_scores = cal_pred
            extra_payload = {
                "weights": [float(v) for v in weights],
                "intercept": intercept,
                "feature_means": [float(v) for v in means],
                "feature_stds": [float(v) for v in stds],
            }

        trained_until = max(row[1] for row in train_rows)
        calibrated_until = max(row[1] for row in cal_rows)
        return QlibAgent.fit_calibration_entry(
            cal_predicted=cal_pred,
            cal_raw_scores=cal_scores,
            cal_actual=cal_y,
            confidence_level=confidence_level,
            training_count=len(train_rows),
            purged_count=split - len(train_rows),
            trained_until=trained_until,
            calibrated_until=calibrated_until,
            extra_payload=extra_payload,
        )

    def _walk_forward_horizon(
        self,
        rows: list[tuple[datetime, datetime, np.ndarray, float]],
        *,
        feature_names: list[str],
        model_type: Literal["lgb", "linear"],
        n_splits: int,
        num_boost_round: int,
        confidence_level: float,
    ) -> tuple[list[WalkForwardFoldReport], dict[str, float]]:
        total = len(rows)
        folds: list[WalkForwardFoldReport] = []
        if total < 90:
            return folds, {}

        min_train = max(80, int(total * 0.5))
        oos_total = total - min_train
        fold_size = max(1, oos_total // n_splits)

        for fold_idx in range(n_splits):
            test_start = min_train + fold_idx * fold_size
            test_end = total if fold_idx == n_splits - 1 else min(total, test_start + fold_size)
            if test_start >= test_end:
                break
            test_rows = rows[test_start:test_end]
            test_start_at = test_rows[0][0]
            eligible_train = [row for row in rows[:test_start] if row[1] < test_start_at]
            if len(eligible_train) < 80:
                continue
            try:
                entry = self._fit_and_calibrate_window(
                    eligible_train,
                    feature_names=feature_names,
                    model_type=model_type,
                    num_boost_round=num_boost_round,
                    confidence_level=confidence_level,
                )
            except ValueError:
                continue

            test_x = np.vstack([row[2] for row in test_rows])
            test_y = np.asarray([row[3] for row in test_rows], dtype=np.float64)
            if model_type == "lgb":
                reg = lgb.Booster(model_str=entry["regressor"])
                clf = lgb.Booster(model_str=entry["classifier"])
                preds = np.asarray(reg.predict(test_x, num_threads=2), dtype=np.float64)
                raw_scores = np.asarray(clf.predict(test_x, raw_score=True, num_threads=2), dtype=np.float64)
            else:
                preds = self._predict_ridge(
                    test_x,
                    np.asarray(entry["weights"], dtype=np.float64),
                    float(entry["intercept"]),
                    np.asarray(entry["feature_means"], dtype=np.float64),
                    np.asarray(entry["feature_stds"], dtype=np.float64),
                )
                raw_scores = preds

            probs = expit(float(entry["probability_slope"]) * raw_scores + float(entry["probability_intercept"]))
            bound = float(entry["residual_bound"])
            lcbs = preds - bound
            actual_pos = (test_y > 0).astype(np.float64)

            folds.append(
                WalkForwardFoldReport(
                    fold_index=len(folds) + 1,
                    train_count=int(entry["training_count"]),
                    test_count=len(test_rows),
                    test_start_at=test_rows[0][0],
                    test_end_at=test_rows[-1][0],
                    mae=round(float(np.mean(np.abs(preds - test_y))), 6),
                    directional_accuracy=round(float(np.mean((preds > 0) == (test_y > 0))), 4),
                    brier_score=round(float(np.mean((probs - actual_pos) ** 2)), 6),
                    mean_predicted_return=round(float(np.mean(preds)), 6),
                    mean_actual_return=round(float(np.mean(test_y)), 6),
                    lcb_coverage=round(float(np.mean(test_y >= lcbs)), 4),
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
        return folds, summary

    def _append_experiment(self, record: QlibExperimentRecord) -> None:
        self._experiment_repo.append(record)

    def get_status(self) -> QlibStatusResponse:
        """返回 Qlib 运行时安装状态、本地/外部数据集清单及实验统计。"""
        local_ds = self._exporter.inspect_dataset(self._provider_uri, source_type="local_sqlite")
        external_ds = (
            self._exporter.inspect_dataset(self._external_provider_uri, source_type="external_qlib")
            if self._external_provider_uri is not None
            else None
        )
        return QlibStatusResponse(
            pyqlib_installed=self._bridge.is_available(),
            pyqlib_version=self._bridge.get_version(),
            qlib_enabled=self._qlib_enabled,
            predictor_backend=self._predictor_backend,
            provider_uri=str(self._provider_uri),
            external_provider_uri=str(self._external_provider_uri) if self._external_provider_uri else None,
            active_model_dir=self._active_model_dir,
            local_dataset=local_ds,
            external_dataset=external_ds,
            experiment_count=self._experiment_repo.count(),
        )

    def export_dataset(self, request: QlibExportRequest, *, target_dir: Path | None = None) -> QlibExportReport:
        """将本地 SQLite 历史行情导出为标准 Qlib 二进制目录。"""
        resolved_dir = target_dir or (Path(request.output_dir) if request.output_dir else self._provider_uri)
        return self._exporter.export_local_dataset(request, target_dir=resolved_dir)

    def evaluate_factors(
        self,
        request: QlibFactorEvaluateRequest,
        *,
        provider_uri_override: Path | None = None,
    ) -> QlibFactorEvaluateReport:
        """调用 Qlib 表达式引擎评估各因子对扣费净收益标签的 IC、RankIC 与覆盖率。"""
        provider_uri = provider_uri_override or self._resolve_provider_uri(request.source_type, request.provider_uri)
        instruments = self._resolve_instruments(provider_uri, request.source_type, request.instruments)
        specs = QlibFactorLibrary.get_factors(
            request.factor_preset,
            freq=request.freq,
            custom_expressions=request.custom_expressions,
        )
        label_expr = QlibFactorLibrary.build_net_return_label_expression(
            horizon=request.horizon,
            freq=request.freq,
            fee_rule=self._fee_rule,
            source_type=request.source_type,
        )
        expressions = [spec.expression for spec in specs] + [label_expr]
        frame = self._bridge.fetch_features(
            provider_uri=provider_uri,
            instruments=instruments,
            expressions=expressions,
            freq=request.freq,
            start_time=request.start_time,
            end_time=request.end_time,
            experiment_dir=self._experiment_dir,
        )
        values = np.asarray(frame.to_numpy(dtype=np.float64), dtype=np.float64)
        total_rows = len(values)
        labels = values[:, -1] if total_rows > 0 else np.array([], dtype=np.float64)

        metrics: list[QlibFactorMetric] = []
        for idx, spec in enumerate(specs):
            col = values[:, idx] if total_rows > 0 else np.array([], dtype=np.float64)
            valid_mask = np.isfinite(col) & np.isfinite(labels)
            valid_count = int(np.sum(valid_mask))
            coverage = round(valid_count / total_rows, 4) if total_rows > 0 else 0.0
            ic_val: float | None = None
            rank_ic_val: float | None = None
            icir_val: float | None = None
            if valid_count >= 5:
                x = col[valid_mask]
                y = labels[valid_mask]
                if float(np.std(x)) > 1e-12 and float(np.std(y)) > 1e-12:
                    corr = float(np.corrcoef(x, y)[0, 1])
                    if math.isfinite(corr):
                        ic_val = round(corr, 6)
                    spearman_res: Any = spearmanr(x, y)
                    sp_corr = float(getattr(spearman_res, "statistic", spearman_res[0]))
                    if math.isfinite(sp_corr):
                        rank_ic_val = round(sp_corr, 6)
                    # 按时间顺序切分 5 段计算分段 IC 均值/标准差作为 ICIR
                    chunks = np.array_split(np.column_stack([x, y]), min(5, max(1, valid_count // 10)))
                    chunk_ics: list[float] = []
                    for chunk in chunks:
                        if (
                            len(chunk) >= 3
                            and float(np.std(chunk[:, 0])) > 1e-12
                            and float(np.std(chunk[:, 1])) > 1e-12
                        ):
                            c = float(np.corrcoef(chunk[:, 0], chunk[:, 1])[0, 1])
                            if math.isfinite(c):
                                chunk_ics.append(c)
                    if len(chunk_ics) >= 2:
                        ic_std = statistics.stdev(chunk_ics)
                        if ic_std > 1e-12:
                            icir_val = round(statistics.mean(chunk_ics) / ic_std, 4)
            metrics.append(
                QlibFactorMetric(
                    factor_name=spec.name,
                    expression=spec.expression,
                    sample_count=valid_count,
                    coverage_ratio=coverage,
                    ic=ic_val,
                    rank_ic=rank_ic_val,
                    icir=icir_val,
                )
            )

        return QlibFactorEvaluateReport(
            source_type=request.source_type,
            provider_uri=str(provider_uri),
            freq=request.freq,
            horizon=request.horizon,
            factor_preset=request.factor_preset,
            instruments=instruments,
            metrics=metrics,
        )

    def train_and_record(
        self,
        request: QlibTrainRequest,
        *,
        provider_uri_override: Path | None = None,
        output_model_dir_override: Path | None = None,
    ) -> QlibTrainReport:
        """执行 Qlib 特征提取、Purged Walk-Forward 验证、概率/LCB 校准与实验记录。"""
        provider_uri = provider_uri_override or self._resolve_provider_uri(request.source_type, request.provider_uri)
        instruments = self._resolve_instruments(provider_uri, request.source_type, request.instruments)
        specs = QlibFactorLibrary.get_factors(
            request.factor_preset,
            freq=request.freq,
            custom_expressions=request.custom_expressions,
        )
        feature_names = [spec.name for spec in specs]
        serving_compatible = request.source_type == "local_sqlite" and request.factor_preset == "gold_core"

        sample_counts: dict[str, int] = {}
        folds_by_horizon: dict[str, list[WalkForwardFoldReport]] = {}
        oos_summary: dict[str, dict[str, float]] = {}
        calibrated_entries: dict[str, dict[str, Any]] = {}

        for horizon in request.horizons:
            label_expr = QlibFactorLibrary.build_net_return_label_expression(
                horizon=horizon,
                freq=request.freq,
                fee_rule=self._fee_rule,
                source_type=request.source_type,
                spread_pct=request.spread_pct,
            )
            frame = self._bridge.fetch_features(
                provider_uri=provider_uri,
                instruments=instruments,
                expressions=[spec.expression for spec in specs] + [label_expr],
                freq=request.freq,
                start_time=request.start_time,
                end_time=request.end_time,
                experiment_dir=self._experiment_dir,
            )
            horizon_step = QlibFactorLibrary.horizon_steps(horizon, request.freq)
            rows = self._extract_matrix_rows(
                frame,
                feature_count=len(feature_names),
                horizon_step=horizon_step,
                freq=request.freq,
            )
            sample_counts[horizon.value] = len(rows)
            folds, summary = self._walk_forward_horizon(
                rows,
                feature_names=feature_names,
                model_type=request.model_type,
                n_splits=request.n_splits,
                num_boost_round=request.num_boost_round,
                confidence_level=request.confidence_level,
            )
            folds_by_horizon[horizon.value] = folds
            if summary:
                oos_summary[horizon.value] = summary

            if output_model_dir_override is not None or request.output_model_dir is not None:
                calibrated_entries[horizon.value] = self._fit_and_calibrate_window(
                    rows,
                    feature_names=feature_names,
                    model_type=request.model_type,
                    num_boost_round=request.num_boost_round,
                    confidence_level=request.confidence_level,
                )

        run_id = f"qlib_{uuid4().hex[:12]}"
        flat_metrics = {f"{h}_{k}": v for h, m in oos_summary.items() for k, v in m.items()}
        recorder_id = self._bridge.log_recorder_metrics(
            experiment_name=request.experiment_name,
            params={
                "source_type": request.source_type,
                "model_type": request.model_type,
                "factor_preset": request.factor_preset,
                "freq": request.freq,
                "horizons": ",".join(h.value for h in request.horizons),
            },
            metrics=flat_metrics,
        )

        trained_model_info: dict[str, object] | None = None
        model_dir_str: str | None = None
        target_model_dir = output_model_dir_override or (
            Path(request.output_model_dir) if request.output_model_dir else None
        )
        if target_model_dir is not None:
            saved_dir = QlibAgent.save_artifact(
                target_model_dir,
                model_type=request.model_type,
                features=feature_names,
                horizons=calibrated_entries,
                experiment_name=request.experiment_name,
                run_id=run_id,
                source_type=request.source_type,
                factor_preset=request.factor_preset,
                serving_compatible=serving_compatible,
            )
            model_dir_str = str(saved_dir)
            if serving_compatible:
                trained_model_info = QlibAgent(saved_dir).get_model_info()
            else:
                trained_model_info = {
                    "model_version": "qlib_v1",
                    "model_type": request.model_type,
                    "serving_compatible": False,
                    "run_id": run_id,
                    "horizons": list(calibrated_entries.keys()),
                }

        record = QlibExperimentRecord(
            experiment_name=request.experiment_name,
            run_id=run_id,
            source_type=request.source_type,
            provider_uri=str(provider_uri),
            model_type=request.model_type,
            factor_preset=request.factor_preset,
            instruments=instruments,
            freq=request.freq,
            horizons=request.horizons,
            serving_compatible=serving_compatible,
            oos_summary=oos_summary,
            model_output_dir=model_dir_str,
            qlib_recorder_id=recorder_id,
            created_at=datetime.now(UTC),
        )
        self._append_experiment(record)
        return QlibTrainReport(
            experiment=record,
            sample_counts=sample_counts,
            walk_forward_folds=folds_by_horizon,
            trained_model_info=trained_model_info,
        )

    def list_experiments(self, *, limit: int = 50) -> list[QlibExperimentRecord]:
        """按创建时间倒序读取本地登记的 Qlib 实验记录。"""
        return self._experiment_repo.list_recent(limit=limit)
