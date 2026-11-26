"""PIT 训练样本构建、Walk-Forward 样本外滚动验证与 LightGBM 训练闭环服务。"""

from __future__ import annotations

import bisect
import math
import operator
import statistics
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from src.agents.lightgbm import LightGBMAgent, TrainingSample
from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.reference_quote import ReferenceQuote
from src.contracts.enums.trading import DataQualityState, HoldingPeriod
from src.contracts.schemas.model import TrainingPipelineReport, WalkForwardFoldReport
from src.repositories.quote import QuoteRepository
from src.services.decision.cost_calculator import CostCalculator
from src.services.market.live_quotes import LiveQuoteCollector
from src.services.prediction.feature_engine import FeatureEngine


class TrainingPipelineService:
    """从 SQLite 历史行情自动生成 PIT 样本、执行滚动样本外评估并产出 LightGBM 模型。"""

    _HORIZON_HOURS: dict[HoldingPeriod, int] = {
        HoldingPeriod.H1: 1,
        HoldingPeriod.H4: 4,
        HoldingPeriod.D1: 24,
        HoldingPeriod.D3: 72,
        HoldingPeriod.W1: 168,
        HoldingPeriod.W2: 336,
        HoldingPeriod.M1: 720,
    }

    def __init__(
        self,
        quotes: QuoteRepository,
        feature_engine: FeatureEngine,
        *,
        live_quotes: LiveQuoteCollector | None = None,
        fee_rule: FeeRule | None = None,
        basis_reference_product: str = "SGE-Au99.99",
    ) -> None:
        self._quotes = quotes
        self._features = feature_engine
        self._live_quotes = live_quotes
        self._calculator = CostCalculator(fee_rule)
        self._basis_reference_product = basis_reference_product

    @staticmethod
    def _reference_to_quote(reference: ReferenceQuote, *, spread_pct: float) -> QuoteRaw:
        """将单边 CNY/g 参考行情按配置点差转换为内存研究用双边报价。"""
        half = spread_pct / 2.0
        source_time = reference.quote_at or reference.received_at
        return QuoteRaw(
            quote_id=f"train_ref_{reference.product_code}_{reference.quote_id}",
            source_id=f"TRAIN_REF_{reference.product_code}",
            product_code=reference.product_code,
            bid_price=round(reference.price * (1.0 - half), 4),
            ask_price=round(reference.price * (1.0 + half), 4),
            quote_at=source_time,
            received_at=reference.received_at,
            available_at=reference.available_at or reference.received_at,
            is_tradable=True,
            is_synthetic=True,
            payload_hash=reference.payload_hash,
            quality_state=DataQualityState.NORMAL,
        )

    def _load_source_quotes(
        self,
        *,
        product_code: str,
        reference_product_code: str | None,
        spread_pct: float,
        exclude_synthetic: bool,
    ) -> tuple[list[QuoteRaw], Literal["quotes", "reference"], str]:
        """按模式加载严格递增、质量正常的报价序列。"""
        if reference_product_code is not None:
            if self._live_quotes is None:
                raise ValueError("未配置参考行情采集器，无法从参考行情库构建样本")
            refs = self._live_quotes.query_series(reference_product_code, limit=None)
            raw_quotes = [
                self._reference_to_quote(item, spread_pct=spread_pct)
                for item in refs
                if item.unit == "CNY/g"
                and item.price > 0
                and item.quality_state not in (DataQualityState.ANOMALY, DataQualityState.STALE)
            ]
            source_mode: Literal["quotes", "reference"] = "reference"
            effective_code = reference_product_code
        else:
            now_utc = datetime.now(UTC)
            raw_quotes = self._quotes.query_pit_series(
                start_time=datetime(1970, 1, 1, tzinfo=UTC),
                end_time=now_utc,
                as_of=now_utc,
                product_code=product_code,
                exclude_synthetic=exclude_synthetic,
            )
            source_mode = "quotes"
            effective_code = product_code

        deduped: list[QuoteRaw] = []
        last_ts: datetime | None = None
        for quote in sorted(raw_quotes, key=lambda q: q.quote_at):
            if (
                quote.quality_state != DataQualityState.NORMAL
                or not math.isfinite(quote.bid_price)
                or not math.isfinite(quote.ask_price)
                or quote.bid_price <= 0
                or quote.ask_price < quote.bid_price
            ):
                continue
            if last_ts is not None and quote.quote_at <= last_ts:
                continue
            deduped.append(quote)
            last_ts = quote.quote_at
        return deduped, source_mode, effective_code

    def _load_basis_series(self) -> tuple[list[datetime], list[float]]:
        """加载基准参考价序列，用于按 PIT 时点查找最新内外盘基差参考价。"""
        if self._live_quotes is None:
            return [], []
        try:
            refs = self._live_quotes.query_series(self._basis_reference_product, limit=None)
        except ValueError:
            return [], []
        pairs: list[tuple[datetime, float]] = []
        for item in refs:
            if item.unit != "CNY/g" or item.price <= 0 or item.quality_state == DataQualityState.ANOMALY:
                continue
            ts = item.available_at or item.received_at
            ts_utc = ts.astimezone(UTC) if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
            pairs.append((ts_utc, item.price))
        pairs.sort(key=operator.itemgetter(0))
        times = [pair[0] for pair in pairs]
        prices = [pair[1] for pair in pairs]
        return times, prices

    @staticmethod
    def _lookup_basis_price(times: list[datetime], prices: list[float], as_of: datetime) -> float | None:
        if not times:
            return None
        as_of_utc = as_of.astimezone(UTC) if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
        idx = bisect.bisect_right(times, as_of_utc) - 1
        return prices[idx] if idx >= 0 else None

    def build_samples(
        self,
        *,
        product_code: str = "GOLD_ACCUMULATION",
        reference_product_code: str | None = None,
        spread_pct: float = 0.002,
        horizons: list[HoldingPeriod] | None = None,
        exclude_synthetic: bool = True,
    ) -> tuple[list[TrainingSample], Literal["quotes", "reference"], str]:
        """按严格 PIT 因果边界生成扣费净收益标签训练样本。"""
        target_horizons = horizons or [HoldingPeriod.D1, HoldingPeriod.W1]
        series, source_mode, effective_code = self._load_source_quotes(
            product_code=product_code,
            reference_product_code=reference_product_code,
            spread_pct=spread_pct,
            exclude_synthetic=exclude_synthetic,
        )
        if len(series) < 2:
            return [], source_mode, effective_code

        quote_times = [q.quote_at for q in series]
        basis_times, basis_prices = self._load_basis_series()
        samples: list[TrainingSample] = []
        pit_times = [max(q.quote_at, q.received_at, q.available_at or q.received_at) for q in series]
        monotonic_pit = all(pit_times[i] >= pit_times[i - 1] for i in range(1, len(pit_times)))
        stream = self._features.create_stream() if monotonic_pit and hasattr(self._features, "create_stream") else None

        for idx, entry_q in enumerate(series):
            as_of = pit_times[idx]
            basis_ref = self._lookup_basis_price(basis_times, basis_prices, as_of)
            exits: dict[HoldingPeriod, tuple[QuoteRaw, float]] = {}

            for horizon in target_horizons:
                target_end = entry_q.quote_at + timedelta(hours=self._HORIZON_HOURS[horizon])
                exit_idx = bisect.bisect_left(quote_times, target_end, lo=idx + 1)
                if exit_idx >= len(series):
                    continue
                exit_q = series[exit_idx]
                net_ret = self._calculator.net_return(entry_ask=entry_q.ask_price, exit_bid=exit_q.bid_price)
                if not math.isfinite(net_ret) or net_ret <= -1.0:
                    continue
                exits[horizon] = (exit_q, net_ret)

            if stream is not None:
                feats_by_horizon = stream.push_and_extract(
                    entry_q,
                    as_of=as_of,
                    horizons=tuple(exits.keys()),
                    intl_ref_cny=basis_ref,
                )
            elif exits:
                history = series[: idx + 1]
                feats_by_horizon = {
                    horizon: self._features.extract_features(
                        history,
                        intl_ref_cny=basis_ref,
                        as_of=as_of,
                        horizon=horizon,
                    )
                    for horizon in exits
                }
            else:
                continue

            for horizon, (exit_q, net_ret) in exits.items():
                # 确保特征时间锚定在入场报价时刻，且标签覆盖目标持有期
                features = feats_by_horizon[horizon].model_copy(update={"as_of": entry_q.quote_at})
                samples.append(
                    TrainingSample(
                        features=features,
                        horizon=horizon,
                        net_return=round(net_ret, 8),
                        label_end_at=exit_q.quote_at,
                    )
                )

        return samples, source_mode, effective_code

    @staticmethod
    def export_samples_jsonl(samples: list[TrainingSample], output_path: str | Path) -> Path:
        """将构建好的 PIT 样本导出为 LightGBMAgent 标准 JSONL 格式。"""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [sample.model_dump_json() for sample in samples]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return path

    @staticmethod
    def walk_forward_evaluate(
        samples: list[TrainingSample],
        *,
        n_splits: int = 3,
        confidence_level: float = 0.9,
        num_boost_round: int = 80,
    ) -> tuple[dict[str, list[WalkForwardFoldReport]], dict[str, dict[str, float]]]:
        """执行扩张窗口时间滚动（Walk-Forward）样本外验证，自动清除跨分割点标签。"""
        grouped: dict[HoldingPeriod, list[TrainingSample]] = {}
        for sample in samples:
            grouped.setdefault(sample.horizon, []).append(sample)

        folds_by_horizon: dict[str, list[WalkForwardFoldReport]] = {}
        summary_by_horizon: dict[str, dict[str, float]] = {}

        for horizon, rows in grouped.items():
            ordered = sorted(rows, key=lambda r: r.features.as_of)
            total = len(ordered)
            folds: list[WalkForwardFoldReport] = []
            # 至少预留 80 条样本供初始训练+校准，剩余切分为 n_splits 折样本外测试
            if total >= 90:
                min_train = max(80, int(total * 0.5))
                oos_total = total - min_train
                fold_size = max(1, oos_total // n_splits)

                for fold_idx in range(n_splits):
                    test_start = min_train + fold_idx * fold_size
                    test_end = total if fold_idx == n_splits - 1 else min(total, test_start + fold_size)
                    if test_start >= test_end:
                        break
                    test_rows = ordered[test_start:test_end]
                    test_start_at = test_rows[0].features.as_of
                    train_rows = [row for row in ordered[:test_start] if row.label_end_at < test_start_at]
                    if len(train_rows) < 80:
                        continue

                    try:
                        with tempfile.TemporaryDirectory() as tmp_dir:
                            agent = LightGBMAgent.train(
                                train_rows,
                                tmp_dir,
                                confidence_level=confidence_level,
                                num_boost_round=num_boost_round,
                            )
                    except ValueError:
                        continue

                    abs_errors: list[float] = []
                    hits: list[float] = []
                    briers: list[float] = []
                    pred_returns: list[float] = []
                    actual_returns: list[float] = []
                    lcb_hits: list[float] = []

                    for row in test_rows:
                        pred = agent.predict(row.features, horizon=horizon)
                        actual = row.net_return
                        abs_errors.append(abs(pred.expected_net_return - actual))
                        hits.append(1.0 if (pred.expected_net_return > 0) == (actual > 0) else 0.0)
                        briers.append((pred.win_probability - (1.0 if actual > 0 else 0.0)) ** 2)
                        pred_returns.append(pred.expected_net_return)
                        actual_returns.append(actual)
                        lcb_hits.append(1.0 if actual >= pred.net_return_lcb else 0.0)

                    folds.append(
                        WalkForwardFoldReport(
                            fold_index=len(folds) + 1,
                            train_count=len(train_rows),
                            test_count=len(test_rows),
                            test_start_at=test_rows[0].features.as_of,
                            test_end_at=test_rows[-1].features.as_of,
                            mae=round(statistics.mean(abs_errors), 6),
                            directional_accuracy=round(statistics.mean(hits), 4),
                            brier_score=round(statistics.mean(briers), 6),
                            mean_predicted_return=round(statistics.mean(pred_returns), 6),
                            mean_actual_return=round(statistics.mean(actual_returns), 6),
                            lcb_coverage=round(statistics.mean(lcb_hits), 4),
                        )
                    )

            folds_by_horizon[horizon.value] = folds
            if folds:
                summary_by_horizon[horizon.value] = {
                    "valid_folds": float(len(folds)),
                    "avg_mae": round(statistics.mean(f.mae for f in folds), 6),
                    "avg_directional_accuracy": round(statistics.mean(f.directional_accuracy for f in folds), 4),
                    "avg_brier_score": round(statistics.mean(f.brier_score for f in folds), 6),
                    "avg_lcb_coverage": round(statistics.mean(f.lcb_coverage for f in folds), 4),
                }

        return folds_by_horizon, summary_by_horizon

    def run_pipeline(
        self,
        *,
        product_code: str = "GOLD_ACCUMULATION",
        reference_product_code: str | None = None,
        spread_pct: float = 0.002,
        horizons: list[HoldingPeriod] | None = None,
        n_splits: int = 3,
        num_boost_round: int = 80,
        confidence_level: float = 0.9,
        export_samples_path: str | None = None,
        output_model_dir: str | None = None,
        exclude_synthetic: bool = True,
    ) -> TrainingPipelineReport:
        """一键串联 PIT 样本构建、导出、Walk-Forward 样本外验证与最终模型训练。"""
        target_horizons = horizons or [HoldingPeriod.D1, HoldingPeriod.W1]
        samples, source_mode, effective_code = self.build_samples(
            product_code=product_code,
            reference_product_code=reference_product_code,
            spread_pct=spread_pct,
            horizons=target_horizons,
            exclude_synthetic=exclude_synthetic,
        )
        counts: dict[str, int] = {h.value: 0 for h in target_horizons}
        for sample in samples:
            counts[sample.horizon.value] = counts.get(sample.horizon.value, 0) + 1

        exported_path: str | None = None
        if export_samples_path is not None:
            exported_path = str(self.export_samples_jsonl(samples, export_samples_path))

        folds, oos_summary = self.walk_forward_evaluate(
            samples,
            n_splits=n_splits,
            confidence_level=confidence_level,
            num_boost_round=num_boost_round,
        )

        trained_info: dict[str, object] | None = None
        model_dir_str: str | None = None
        if output_model_dir is not None:
            if not samples:
                raise ValueError("未生成有效 PIT 训练样本，无法训练 LightGBM 模型")
            agent = LightGBMAgent.train(
                samples,
                output_model_dir,
                confidence_level=confidence_level,
                num_boost_round=num_boost_round,
            )
            trained_info = agent.get_model_info()
            model_dir_str = str(Path(output_model_dir))

        return TrainingPipelineReport(
            product_code=effective_code,
            source_mode=source_mode,
            horizons=target_horizons,
            sample_counts=counts,
            samples_output_path=exported_path,
            model_output_dir=model_dir_str,
            walk_forward_folds=folds,
            oos_summary=oos_summary,
            trained_model_info=trained_info,
        )
