"""规则型元决策引擎服务。

整合数据质量门禁、宏观事件锁定、周末风控窗口、分层模型预测与持仓状态。
按严格优先级输出标准化交易决策与原因码。
"""

import math
import uuid
from datetime import UTC, datetime

from src.services.cost_calculator import CostCalculator
from src.services.multi_window import MultiWindowFusion
from src.services.news_risk import NewsRiskEngine
from src.services.session_calendar import SessionCalendar
from src.types.domain.account import AccountSnapshot
from src.types.domain.decision import Decision
from src.types.domain.feature import ModelPrediction
from src.types.domain.product import FeeRule
from src.types.domain.quote import QuoteRaw
from src.types.enums.trading import DataQualityState, DecisionAction, HoldingPeriod


class DecisionEngine:
    """规则型元决策（Meta-Decision）引擎。"""

    def __init__(
        self,
        edge_threshold: float = 0.001,
        calendar: SessionCalendar | None = None,
        news_engine: NewsRiskEngine | None = None,
        fusion_engine: MultiWindowFusion | None = None,
        fee_rule: FeeRule | None = None,
    ) -> None:
        self._calculator = CostCalculator(fee_rule)
        self._edge_th: float = edge_threshold
        self._calendar: SessionCalendar = calendar or SessionCalendar()
        self._news_engine: NewsRiskEngine = news_engine or NewsRiskEngine()
        self._fusion: MultiWindowFusion = fusion_engine or MultiWindowFusion()

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def evaluate_decision(
        self,
        quote: QuoteRaw,
        predictions: dict[HoldingPeriod, ModelPrediction],
        account: AccountSnapshot,
        target_buy_grams: float = 1.0,
        as_of: datetime | None = None,
    ) -> Decision:
        """执行完整分层决策流水线并生成不可变决策指令契约。"""
        dec_id = f"dec_{uuid.uuid4().hex[:12]}"
        now_dt = as_of or quote.quote_at
        reasons: list[str] = []
        risk_flags: list[str] = []

        quality_state = quote.quality_state
        if (
            not all(math.isfinite(p) for p in (quote.bid_price, quote.ask_price))
            or quote.bid_price <= 0
            or quote.ask_price < quote.bid_price
        ):
            quality_state = DataQualityState.ANOMALY

        # 数据与事件门禁共用弃权出口，避免各分支的持仓字段漂移。
        if quality_state != DataQualityState.NORMAL:
            reasons.append(f"DATA_QUALITY_FAIL_{quality_state.value}")
            risk_flags.append("DATA_QUALITY_BREACH")
        elif not quote.is_tradable:
            reasons.append("MARKET_NOT_TRADABLE")
        elif self._news_engine.is_event_locked(now_dt)[0]:
            reasons.append("EVENT_LOCK_ACTIVE")
            risk_flags.append("EVENT_LOCK")

        primary_pred: ModelPrediction | None = None
        best_horizon = HoldingPeriod.W1
        if not reasons:
            try:
                best_horizon, primary_pred = self._fusion.select_primary_horizon(predictions)
            except ValueError:
                reasons.append("NO_VALID_PREDICTIONS")

        if primary_pred is None:
            return Decision(
                decision_id=dec_id,
                decision_at=now_dt,
                action=DecisionAction.ABSTAIN,
                delta_g=0.0,
                target_inventory_g=account.gold_total_g,
                horizon_primary=best_horizon,
                risk_flags=risk_flags,
                quality_state=quality_state.value,
                reason_codes=reasons,
            )

        if self._calendar.is_in_weekend_risk_window(now_dt):
            risk_flags.append("WEEKEND_RISK_WINDOW")

        # 5. 开仓与退出逻辑判定
        action = DecisionAction.HOLD
        delta_g = 0.0
        target_inv = account.gold_total_g

        # 开仓条件：置信下界 LCB 显著覆盖门槛阈值，且不在周末禁入窗口
        if primary_pred.net_return_lcb > self._edge_th:
            if "WEEKEND_RISK_WINDOW" in risk_flags:
                reasons.append("BLOCKED_BY_WEEKEND_RISK_WINDOW")
                action = DecisionAction.ABSTAIN
            else:
                action = DecisionAction.BUY
                delta_g = target_buy_grams
                target_inv = account.gold_total_g + delta_g
                reasons.append(f"EDGE_EXCEEDS_THRESHOLD_LCB_{primary_pred.net_return_lcb:.4f}")
        elif account.gold_available_g > 0 and primary_pred.expected_net_return < -0.005:
            # 退出条件：持有持仓且预期显著恶化时触发平仓
            action = DecisionAction.SELL
            delta_g = -account.gold_available_g
            target_inv = account.gold_total_g + delta_g
            reasons.append(f"EXIT_DUE_TO_NEGATIVE_OUTLOOK_{primary_pred.expected_net_return:.4f}")
        else:
            action = DecisionAction.HOLD
            reasons.append("NO_SIGNIFICANT_EDGE")

        return Decision(
            decision_id=dec_id,
            decision_at=now_dt,
            action=action,
            delta_g=delta_g,
            target_inventory_g=target_inv,
            horizon_primary=best_horizon,
            expected_net_return=primary_pred.expected_net_return,
            net_return_lcb=primary_pred.net_return_lcb,
            win_probability=primary_pred.win_probability,
            expected_cost=round(
                self._calculator.total_round_trip_cost_pct((quote.ask_price - quote.bid_price) / quote.ask_price), 6
            ),
            uncertainty=primary_pred.uncertainty,
            risk_flags=risk_flags,
            quality_state=quote.quality_state.value,
            reason_codes=reasons,
        )
