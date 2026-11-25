"""历史 PIT 回测与被动基准横向对比编排服务。

从 PIT 行情仓储或离线 JSONL 文件加载历史报价，串联 ShadowRunner 与 BaselineEngine，
输出包含超额收益指标的结构化回测汇总报告。
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.contracts.domains.benchmark import BacktestSummary
from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState
from src.repositories.quote import QuoteRepository
from src.services.decision.baseline import BaselineEngine
from src.services.decision.decision_engine import DecisionEngine
from src.services.execution.rfq_execution import RFQExecutionEngine
from src.services.execution.shadow_runner import ShadowRunner
from src.services.prediction.feature_engine import FeatureEngine
from src.services.prediction.predictor import ModelPredictor
from src.services.risk.risk_engine import RiskEngine


class BacktestService:
    """历史回测与被动基准对比统一编排服务。"""

    def __init__(
        self,
        quotes: QuoteRepository,
        feature_engine: FeatureEngine,
        predictor: ModelPredictor,
        decision_engine: DecisionEngine,
        risk_engine: RiskEngine,
        fee_rule: FeeRule,
    ) -> None:
        self._quotes = quotes
        self._features = feature_engine
        self._predictor = predictor
        self._decision = decision_engine
        self._risk = risk_engine
        self._fee_rule = fee_rule

    @staticmethod
    def _to_utc(dt: datetime) -> datetime:
        """统一补齐时区为 UTC，确保离线文件与仓储时间边界比较一致。"""
        return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    def _load_quotes_from_file(
        self,
        quotes_file: Path,
        product_code: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[QuoteRaw]:
        """从离线 JSONL 文件加载满足时间区间与标的过滤条件的行情。

        统一按 PIT 截止时间（源时间、接收时间、可用时间三者的最大值）过滤，
        与仓储的 pit_cutoff_utc 保持一致，避免纳入截止时间后才收到的报价。
        """
        if not quotes_file.exists():
            raise FileNotFoundError(f"未找到回测行情文件：{quotes_file}")
        start_utc = self._to_utc(start_time) if start_time is not None else None
        end_utc = self._to_utc(end_time) if end_time is not None else None
        loaded: list[QuoteRaw] = []
        with quotes_file.open(encoding="utf-8") as stream:
            for line in stream:
                clean = line.strip()
                if not clean:
                    continue
                quote = QuoteRaw.model_validate(json.loads(clean))
                if product_code is not None and quote.product_code != product_code:
                    continue
                if quote.quality_state != DataQualityState.NORMAL:
                    continue
                quote_at_utc = self._to_utc(quote.quote_at)
                received_utc = self._to_utc(quote.received_at)
                available_utc = self._to_utc(quote.available_at) if quote.available_at is not None else received_utc
                pit_cutoff = max(quote_at_utc, received_utc, available_utc)
                if start_utc is not None and quote_at_utc < start_utc:
                    continue
                if end_utc is not None and (quote_at_utc > end_utc or pit_cutoff > end_utc):
                    continue
                loaded.append(quote)
        loaded.sort(
            key=lambda q: max(
                self._to_utc(q.quote_at),
                self._to_utc(q.received_at),
                self._to_utc(q.available_at or q.received_at),
            )
        )
        return loaded

    def _load_quotes_from_repo(
        self,
        product_code: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[QuoteRaw]:
        """从 PIT 仓储按时间顺序回放历史行情。"""
        effective_end = end_time or datetime.now(UTC)
        effective_start = start_time or (effective_end - timedelta(days=3650))
        if effective_start > effective_end:
            raise ValueError("回测起始时间不得晚于结束时间")
        return list(
            self._quotes.iter_replay(
                start_time=effective_start,
                end_time=effective_end,
                product_code=product_code,
            )
        )

    def run(
        self,
        *,
        product_code: str = "GOLD_ACCUMULATION",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        quotes_file: Path | str | None = None,
        initial_cash: float = 100000.0,
        trade_grams_per_order: float = 2.0,
        dca_step_interval: int = 5,
    ) -> BacktestSummary:
        """执行全链路影子回测并与被动基准策略横向对比。"""
        if quotes_file is not None:
            quotes = self._load_quotes_from_file(
                Path(quotes_file),
                product_code=product_code,
                start_time=start_time,
                end_time=end_time,
            )
        else:
            quotes = self._load_quotes_from_repo(
                product_code=product_code,
                start_time=start_time,
                end_time=end_time,
            )

        runner = ShadowRunner(
            initial_cash=initial_cash,
            feature_engine=self._features,
            predictor=self._predictor,
            decision_engine=self._decision,
            risk_engine=self._risk,
            rfq_engine=RFQExecutionEngine(fee_rule=self._fee_rule),
            fee_rule=self._fee_rule,
        )
        shadow_report = runner.run_shadow_simulation(
            quotes=quotes,
            trade_grams_per_order=trade_grams_per_order,
        )

        baseline_engine = BaselineEngine(
            initial_cash=initial_cash,
            fee_rule=self._fee_rule,
        )
        baseline_report = baseline_engine.compare_all(
            quotes=quotes,
            dca_step_interval=dca_step_interval,
        )

        metrics_by_name = {item.strategy_name: item for item in baseline_report.metrics}
        bh_metrics = metrics_by_name.get("BUY_AND_HOLD")
        dca_metrics = metrics_by_name.get("DCA")
        excess_bh = (
            round(shadow_report.total_return_pct - bh_metrics.total_return_pct, 6) if bh_metrics is not None else None
        )
        excess_dca = (
            round(shadow_report.total_return_pct - dca_metrics.total_return_pct, 6) if dca_metrics is not None else None
        )

        return BacktestSummary(
            product_code=product_code,
            quote_count=len(quotes),
            shadow_report=shadow_report,
            baseline_report=baseline_report,
            excess_return_vs_buy_and_hold=excess_bh,
            excess_return_vs_dca=excess_dca,
        )
