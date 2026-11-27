"""TimesFM 多因子严格样本外 PIT 回测：预测层指标评估与交易层影子仿真。"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import spearmanr

from src.agents.timesfm import TimesFMAgent
from src.agents.timesfm_multifactor import TimesFMMultiFactorAgent
from src.contracts.domains.benchmark import BacktestSummary
from src.contracts.domains.feature import FeatureSet, ModelPrediction
from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.timesfm_multifactor import (
    TimesFMDatasetManifest,
    TimesFMForecastDistribution,
    TimesFMHorizonBacktestMetrics,
)
from src.contracts.enums.trading import DataQualityState, HoldingPeriod
from src.contracts.exceptions import TimesFMDatasetError
from src.contracts.schemas.timesfm_multifactor import TimesFMBacktestReport, TimesFMBacktestRequest
from src.services.decision.baseline import BaselineEngine
from src.services.decision.decision_engine import DecisionEngine
from src.services.execution.rfq_execution import RFQExecutionEngine
from src.services.execution.shadow_runner import ShadowRunner
from src.services.market.session_calendar import SessionCalendar
from src.services.prediction.feature_engine import FeatureEngine, IncrementalFeatureStream
from src.services.prediction.predictor import ModelPredictor
from src.services.risk.risk_engine import RiskEngine
from src.services.timesfm.dataset_builder import TimesFMDatasetBuilder
from src.services.timesfm.trainer import TimesFMTrainer


class _PrecomputedDistributions:
    """按决策时点与持有期保存批量因果预计算的 TimesFM 分布特征。"""

    def __init__(self) -> None:
        self._store: dict[tuple[int, str], TimesFMForecastDistribution] = {}

    def put(self, epoch: float, horizon: HoldingPeriod, dist: TimesFMForecastDistribution) -> None:
        """登记某决策时点的分布特征。"""
        self._store[(round(epoch), horizon.value)] = dist

    def get_cached_distribution(self, as_of: datetime, horizon: HoldingPeriod) -> TimesFMForecastDistribution | None:
        """返回与决策时点对齐的分布特征。"""
        return self._store.get((round(as_of.timestamp()), horizon.value))


class _MappedModel:
    """将影子仿真请求的持有期映射到已训练持有期，并还原预测结果上的持有期标识。"""

    def __init__(self, inner: TimesFMMultiFactorAgent, mapping: dict[HoldingPeriod, HoldingPeriod]) -> None:
        self._inner = inner
        self._mapping = mapping

    def predict(self, features: FeatureSet, horizon: HoldingPeriod) -> ModelPrediction:
        """以映射后的持有期预测，并保留调用方请求的持有期。"""
        mapped = self._mapping.get(horizon, horizon)
        return self._inner.predict(features, mapped).model_copy(update={"horizon": horizon})

    def get_model_info(self) -> dict[str, Any]:
        """返回内部模型元信息。"""
        return self._inner.get_model_info()


class _WarmupFeatureEngine(FeatureEngine):
    """在增量特征流中先灌入预热期历史报价（不触发预测），保证样本外首步拥有完整历史。"""

    def __init__(self, warmup_quotes: list[QuoteRaw], *, calendar: SessionCalendar | None = None) -> None:
        super().__init__(calendar=calendar)
        self._warmup_quotes = warmup_quotes

    def create_stream(self) -> IncrementalFeatureStream:
        """创建已预热的增量特征流。"""
        stream = super().create_stream()
        for quote in self._warmup_quotes:
            stream.push_and_extract(quote, as_of=quote.quote_at, horizons=())
        return stream


class TimesFMBacktestEngine:
    """在严格晚于校准结束的样本外区间评估 TimesFM 多因子模型的预测质量与交易表现。"""

    def __init__(
        self,
        builder: TimesFMDatasetBuilder,
        trainer: TimesFMTrainer,
        timesfm_agent: TimesFMAgent,
        decision_engine: DecisionEngine,
        risk_engine: RiskEngine,
        fee_rule: FeeRule,
        *,
        agent_factory: Callable[[str | None], TimesFMAgent] | None = None,
    ) -> None:
        self._builder = builder
        self._trainer = trainer
        self._agent = timesfm_agent
        self._decision = decision_engine
        self._risk = risk_engine
        self._fee_rule = fee_rule
        self._agent_factory = agent_factory

    def _resolve_eval_agent(self, checkpoint_dir: Path | None) -> TimesFMAgent:
        """回测使用微调权重时构造隔离实例，避免修改全局共享 TimesFMAgent。"""
        if checkpoint_dir is None:
            return self._agent
        if self._agent_factory is not None:
            return self._agent_factory(str(checkpoint_dir))
        return self._agent.clone_isolated(checkpoint_path=str(checkpoint_dir))

    @staticmethod
    def _utc(epoch: float) -> datetime:
        return datetime.fromtimestamp(float(epoch), UTC)

    @staticmethod
    def _to_quotes(
        arrays: dict[str, np.ndarray], manifest: TimesFMDatasetManifest, indices: np.ndarray
    ) -> list[QuoteRaw]:
        synthetic = manifest.source_mode != "quotes"
        quotes: list[QuoteRaw] = []
        for index in indices:
            at = datetime.fromtimestamp(float(arrays["times"][index]), UTC)
            quotes.append(
                QuoteRaw(
                    quote_id=f"tfm_bt_{int(index)}",
                    source_id="TFM_BACKTEST",
                    product_code=manifest.product_code,
                    bid_price=float(arrays["bid"][index]),
                    ask_price=float(arrays["ask"][index]),
                    quote_at=at,
                    received_at=at,
                    available_at=at,
                    is_tradable=True,
                    is_synthetic=synthetic,
                    quality_state=DataQualityState.NORMAL,
                )
            )
        return quotes

    @staticmethod
    def _correlations(pred: np.ndarray, actual: np.ndarray) -> tuple[float | None, float | None]:
        if len(pred) < 3 or float(np.std(pred)) < 1e-15 or float(np.std(actual)) < 1e-15:
            return None, None
        ic = float(np.corrcoef(pred, actual)[0, 1])
        rank = float(getattr(spearmanr(pred, actual), "statistic", float("nan")))
        return (ic if math.isfinite(ic) else None, rank if math.isfinite(rank) else None)

    def _evaluate_horizon(
        self,
        model: TimesFMMultiFactorAgent,
        arrays: dict[str, np.ndarray],
        manifest: TimesFMDatasetManifest,
        horizon: HoldingPeriod,
        window: tuple[float, float],
        *,
        precomputed: dict[int, np.ndarray],
    ) -> TimesFMHorizonBacktestMetrics | None:
        key = horizon.value
        idx = arrays[f"idx_{key}"]
        label = arrays[f"label_{key}"]
        label_end = arrays[f"label_end_{key}"]
        as_of = arrays["times"][idx]
        keep = (as_of > window[0]) & (label_end <= window[1])
        idx, label, as_of = idx[keep], label[keep], as_of[keep]
        if len(idx) == 0:
            return None
        dist = np.vstack([precomputed[int(i)] for i in idx])
        x = np.hstack([dist, arrays["tabular"][idx]])
        names = list(TimesFMMultiFactorAgent.DIST_FEATURE_NAMES) + list(manifest.tabular_factors)
        preds: list[float] = []
        probs: list[float] = []
        lcbs: list[float] = []
        for row, stamp in zip(x, as_of, strict=True):
            result = model.predict_from_factors(
                {name: (None if np.isnan(value) else float(value)) for name, value in zip(names, row, strict=True)},
                horizon,
                as_of=self._utc(float(stamp)),
            )
            preds.append(result.expected_net_return)
            probs.append(result.win_probability)
            lcbs.append(result.net_return_lcb)
        pred_arr = np.asarray(preds)
        actual = label.astype(np.float64)
        ic, rank_ic = self._correlations(pred_arr, actual)
        return TimesFMHorizonBacktestMetrics(
            horizon=horizon,
            sample_count=len(actual),
            mae=round(float(np.mean(np.abs(pred_arr - actual))), 6),
            directional_accuracy=round(float(np.mean((pred_arr > 0) == (actual > 0))), 4),
            brier_score=round(float(np.mean((np.asarray(probs) - (actual > 0)) ** 2)), 6),
            lcb_coverage=round(float(np.mean(actual >= np.asarray(lcbs))), 4),
            ic=ic,
            rank_ic=rank_ic,
            mean_predicted_return=round(float(np.mean(pred_arr)), 6),
            mean_actual_return=round(float(np.mean(actual)), 6),
        )

    def _shadow(
        self,
        model: TimesFMMultiFactorAgent,
        arrays: dict[str, np.ndarray],
        manifest: TimesFMDatasetManifest,
        oos_indices: np.ndarray,
        request: TimesFMBacktestRequest,
        *,
        mapping: dict[HoldingPeriod, HoldingPeriod],
        precomputed_by_horizon: dict[HoldingPeriod, dict[int, np.ndarray]],
    ) -> tuple[BacktestSummary, dict[str, str]]:
        mapped_report = {h.value: m.value for h, m in mapping.items() if h != m}
        provider = _PrecomputedDistributions()
        for mapped in dict.fromkeys(mapping.values()):
            dist_map = precomputed_by_horizon[mapped]
            for index in oos_indices:
                row = dist_map[int(index)]
                provider.put(
                    float(arrays["times"][index]),
                    mapped,
                    TimesFMForecastDistribution(
                        point_return=float(row[0]),
                        q10_return=float(row[1]),
                        q90_return=float(row[2]),
                        iqr_return=float(row[3]),
                        path_mean_return=float(row[4]),
                        path_min_return=float(row[5]),
                        skew=float(row[6]),
                    ),
                )
        bound_model = TimesFMMultiFactorAgent(model.model_dir, timesfm_agent=provider)
        predictor = ModelPredictor(fee_rule=self._fee_rule, model=_MappedModel(bound_model, mapping))
        warmup_start = max(0, int(oos_indices[0]) - manifest.context_length)
        warmup = self._to_quotes(arrays, manifest, np.arange(warmup_start, int(oos_indices[0])))
        runner = ShadowRunner(
            initial_cash=request.initial_cash,
            feature_engine=_WarmupFeatureEngine(warmup, calendar=self._builder.calendar),
            predictor=predictor,
            decision_engine=self._decision,
            risk_engine=self._risk,
            rfq_engine=RFQExecutionEngine(fee_rule=self._fee_rule),
            fee_rule=self._fee_rule,
        )
        quotes = self._to_quotes(arrays, manifest, oos_indices)
        shadow = runner.run_shadow_simulation(quotes=quotes, trade_grams_per_order=request.trade_grams_per_order)
        baseline = BaselineEngine(initial_cash=request.initial_cash, fee_rule=self._fee_rule).compare_all(
            quotes=quotes, dca_step_interval=request.dca_step_interval
        )
        by_name = {item.strategy_name: item for item in baseline.metrics}
        bh = by_name.get("BUY_AND_HOLD")
        dca = by_name.get("DCA")
        summary = BacktestSummary(
            product_code=manifest.product_code,
            quote_count=len(quotes),
            shadow_report=shadow,
            baseline_report=baseline,
            excess_return_vs_buy_and_hold=round(shadow.total_return_pct - bh.total_return_pct, 6) if bh else None,
            excess_return_vs_dca=round(shadow.total_return_pct - dca.total_return_pct, 6) if dca else None,
        )
        return summary, mapped_report

    def run(
        self,
        request: TimesFMBacktestRequest,
        *,
        model_dir: Path,
        dataset_dir: Path,
        run_id: str | None,
    ) -> TimesFMBacktestReport:
        """执行严格样本外回测；回测区间若不晚于模型校准结束则拒绝。"""
        model = TimesFMMultiFactorAgent(model_dir)
        manifest, arrays = self._builder.load_dataset(dataset_dir)
        expected = list(TimesFMMultiFactorAgent.DIST_FEATURE_NAMES) + list(manifest.tabular_factors)
        if list(model.manifest["features"]) != expected:
            raise TimesFMDatasetError("数据集因子与模型特征清单不一致，无法回测")
        calibrated_epoch = model.calibrated_until.timestamp()
        times = arrays["times"]
        if request.start_time is not None and request.start_time.timestamp() <= calibrated_epoch:
            raise TimesFMDatasetError("回测起始时间不得早于或等于模型校准结束时间，否则属于样本内回测")
        start_epoch = max(calibrated_epoch, request.start_time.timestamp() if request.start_time else calibrated_epoch)
        end_epoch = request.end_time.timestamp() if request.end_time else float(times[-1])
        oos = np.where((times > start_epoch) & (times <= end_epoch))[0]
        if len(oos) < 2:
            raise TimesFMDatasetError("样本外区间内没有足够的行情数据")
        use_covariates = bool(model.manifest.get("use_covariates", True))
        eval_agent = self._resolve_eval_agent(model.checkpoint_dir)

        trained = model.horizons
        required = (HoldingPeriod.W1, HoldingPeriod.D1)
        primary = HoldingPeriod.W1 if HoldingPeriod.W1 in trained else trained[0]
        mapping = {h: (h if h in trained else primary) for h in required}
        horizons_to_precompute = list(
            dict.fromkeys([*(h for h in model.horizons if h in manifest.horizons), *mapping.values()])
        )
        # 同一样本外区间仅执行一次 TimesFM 批量推理，供预测指标评估与影子回测共享。
        precomputed_by_horizon: dict[HoldingPeriod, dict[int, np.ndarray]] = {}
        for horizon in horizons_to_precompute:
            steps = manifest.horizon_steps.get(horizon.value) or self._builder.horizon_steps(horizon, manifest.freq)
            dists = self._trainer.extract_distributions(
                arrays,
                manifest,
                oos,
                steps,
                use_covariates=use_covariates,
                batch_size=request.batch_size,
                agent=eval_agent,
            )
            precomputed_by_horizon[horizon] = {int(idx): row for idx, row in zip(oos, dists, strict=True)}

        metrics: dict[str, TimesFMHorizonBacktestMetrics] = {}
        for horizon in model.horizons:
            if horizon not in manifest.horizons:
                continue
            item = self._evaluate_horizon(
                model,
                arrays,
                manifest,
                horizon,
                (start_epoch, end_epoch),
                precomputed=precomputed_by_horizon[horizon],
            )
            if item is not None:
                metrics[horizon.value] = item
        summary, mapped = self._shadow(
            model,
            arrays,
            manifest,
            oos,
            request,
            mapping=mapping,
            precomputed_by_horizon=precomputed_by_horizon,
        )
        return TimesFMBacktestReport(
            run_id=run_id,
            model_info=model.get_model_info(),
            holdout_start_at=self._utc(max(start_epoch, float(times[oos[0]]))),
            holdout_end_at=self._utc(float(times[oos[-1]])),
            horizon_metrics=metrics,
            mapped_horizons=mapped,
            backtest_summary=summary,
        )
