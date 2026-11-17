"""规则型元决策引擎服务。

整合数据质量门禁、宏观事件锁定、周末风控窗口、分层模型预测与持仓状态。
按严格优先级输出标准化交易决策与原因码。
"""

import uuid
from datetime import UTC, datetime

from src.services.multi_window import MultiWindowFusion
from src.services.news_risk import NewsRiskEngine
from src.services.session_calendar import SessionCalendar
from src.types.domain.account import AccountSnapshot
from src.types.domain.decision import Decision
from src.types.domain.feature import ModelPrediction
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
    ) -> None:
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
    ) -> Decision:
        """执行完整分层决策流水线并生成不可变决策指令契约。"""
        dec_id = f"dec_{uuid.uuid4().hex[:12]}"
        now_dt = quote.quote_at or self._now()
        reasons: list[str] = []
        risk_flags: list[str] = []

        # 1. 优先级一：数据质量门禁
        if quote.quality_state != DataQualityState.NORMAL:
            reasons.append(f"DATA_QUALITY_FAIL_{quote.quality_state.value}")
            return Decision(
                decision_id=dec_id,
                decision_at=now_dt,
                action=DecisionAction.ABSTAIN,
                delta_g=0.0,
                target_inventory_g=account.gold_total_g,
                horizon_primary=HoldingPeriod.W1,
                risk_flags=["DATA_QUALITY_BREACH"],
                quality_state=quote.quality_state.value,
                reason_codes=reasons,
            )

        if not quote.is_tradable:
            reasons.append("MARKET_NOT_TRADABLE")
            return Decision(
                decision_id=dec_id,
                decision_at=now_dt,
                action=DecisionAction.ABSTAIN,
                delta_g=0.0,
                target_inventory_g=account.gold_total_g,
                horizon_primary=HoldingPeriod.W1,
                quality_state=quote.quality_state.value,
                reason_codes=reasons,
            )

        # 2. 优先级二：重大宏观事件日历锁定
        locked, lock_reason = self._news_engine.is_event_locked(now_dt)
        if locked and lock_reason:
            reasons.append("EVENT_LOCK_ACTIVE")
            risk_flags.append("EVENT_LOCK")
            return Decision(
                decision_id=dec_id,
                decision_at=now_dt,
                action=DecisionAction.ABSTAIN,
                delta_g=0.0,
                target_inventory_g=account.gold_total_g,
                horizon_primary=HoldingPeriod.W1,
                risk_flags=risk_flags,
                reason_codes=reasons,
            )

        # 3. 优先级三：周末休市前风险窗口期
        if self._calendar.is_in_weekend_risk_window(now_dt):
            risk_flags.append("WEEKEND_RISK_WINDOW")

        # 4. 多窗口动态融合与主评估窗口选择
        best_horizon, primary_pred = self._fusion.select_primary_horizon(predictions)

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
        elif account.gold_total_g > 0 and primary_pred.expected_net_return < -0.005:
            # 退出条件：持有持仓且预期显著恶化时触发平仓
            action = DecisionAction.SELL
            delta_g = -account.gold_available_g
            target_inv = 0.0
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
            expected_cost=round(quote.ask_price - quote.bid_price, 4),
            uncertainty=primary_pred.uncertainty,
            risk_flags=risk_flags,
            quality_state=quote.quality_state.value,
            reason_codes=reasons,
        )
