"""交易提醒与人工确认交互流转服务。

实现基于决策输出向用户生成规范合规提醒，并管理以下状态生命周期：
INTENT_CREATED -> RISK_REJECTED / ALERT_SENT -> EXPIRED / USER_CONFIRMED / USER_DECLINED
-> USER_EXECUTED / NOT_EXECUTED -> RECONCILED / DISCREPANCY
"""

import uuid
from datetime import UTC, datetime, timedelta

from src.contracts.domains.account import AccountSnapshot
from src.contracts.domains.alert import AlertPayload
from src.contracts.domains.decision import Decision
from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import AlertState, DecisionAction
from src.services.decision.cost_calculator import CostCalculator


class AlertService:
    """提醒与人工确认流程管理服务。"""

    def __init__(
        self,
        default_validity_minutes: float = 15.0,
        cost_calculator: CostCalculator | None = None,
        fee_rule: FeeRule | None = None,
    ) -> None:
        self._validity_m: float = default_validity_minutes
        self._calculator: CostCalculator = cost_calculator or CostCalculator(fee_rule)
        self._alerts: dict[str, AlertPayload] = {}

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _compute_break_even_price(
        self,
        action: DecisionAction,
        current_ask: float,
        current_bid: float,
    ) -> float:
        """根据买卖费率与当前价格计算保本盈亏平衡目标价。"""
        if action == DecisionAction.BUY:
            # 买入保本价 = 买入成本 ÷ (1 - 卖出费率)
            return round(self._calculator.break_even_bid(current_ask), 4)
        elif action == DecisionAction.SELL:
            return round(current_bid, 4)
        return round((current_bid + current_ask) / 2.0, 4)

    def _compute_net_exit_loss(
        self,
        current_ask: float,
        spread: float,
        stop_loss_pct: float = 0.005,
    ) -> float:
        """计算每克预估综合净退出损失金额。"""
        spread_pct = spread / current_ask if current_ask > 0 else 0.0
        return round(self._calculator.net_exit_loss_per_gram(current_ask, stop_loss_pct, spread_pct), 4)

    def create_alert(
        self,
        decision: Decision,
        quote: QuoteRaw,
        account: AccountSnapshot,
        validity_minutes: float | None = None,
    ) -> AlertPayload | None:
        """根据风控审核通过的决策建议生成对外提醒。

        若决策为观望（HOLD）或弃权（ABSTAIN）则不生成交易提醒。
        """
        if decision.action not in (DecisionAction.BUY, DecisionAction.SELL):
            return None

        now_dt = self._now()
        val_m = self._validity_m if validity_minutes is None else validity_minutes
        expires_at = now_dt + timedelta(minutes=val_m)
        if decision.expires_at is not None:
            expires_at = min(expires_at, decision.expires_at)
        alert_id = f"alt_{uuid.uuid4().hex[:10]}"

        spread = round(quote.ask_price - quote.bid_price, 4)
        break_even = self._compute_break_even_price(decision.action, quote.ask_price, quote.bid_price)
        net_loss = self._compute_net_exit_loss(quote.ask_price, spread)

        payload = AlertPayload(
            alert_id=alert_id,
            decision_id=decision.decision_id,
            created_at=now_dt,
            expires_at=expires_at,
            action=decision.action,
            suggested_grams=abs(decision.delta_g),
            current_bid_price=quote.bid_price,
            current_ask_price=quote.ask_price,
            spread=spread,
            break_even_price=break_even,
            net_exit_loss_cny=net_loss,
            horizon_primary=decision.horizon_primary,
            reason_codes=decision.reason_codes,
            risk_flags=decision.risk_flags,
            state=AlertState.ALERT_SENT,
            disclaimer="该信号为研究输出，非投资建议",
        )
        self._alerts[alert_id] = payload
        return payload

    def confirm_alert(self, alert_id: str) -> AlertPayload:
        """用户确认交易意向。"""
        alert = self.get_alert(alert_id)
        if not alert:
            raise KeyError(f"未找到指定的提醒条目：{alert_id}")
        if alert.state not in (AlertState.ALERT_SENT, AlertState.USER_CONFIRMED):
            raise ValueError(f"当前提醒状态不可确认为意向：{alert.state.value}")
        if self.is_expired(alert):
            alert.state = AlertState.EXPIRED
            raise ValueError(f"提醒已过有效期：{alert_id}")
        if alert.state != AlertState.ALERT_SENT:
            raise ValueError(f"当前提醒状态不可确认为意向：{alert.state.value}")

        alert.state = AlertState.USER_CONFIRMED
        alert.user_confirmed_at = self._now()
        return alert

    def decline_alert(self, alert_id: str) -> AlertPayload:
        """用户拒绝本次交易建议。"""
        alert = self.get_alert(alert_id)
        if not alert:
            raise KeyError(f"未找到指定的提醒条目：{alert_id}")
        if alert.state not in (AlertState.ALERT_SENT, AlertState.USER_CONFIRMED):
            raise ValueError(f"当前提醒状态不可拒绝建议：{alert.state.value}")
        alert.state = AlertState.USER_DECLINED
        return alert

    def mark_executed(self, alert_id: str) -> AlertPayload:
        """用户标记已在银行界面完成实际下单。"""
        alert = self.get_alert(alert_id)
        if not alert:
            raise KeyError(f"未找到指定的提醒条目：{alert_id}")
        if alert.state not in (AlertState.ALERT_SENT, AlertState.USER_CONFIRMED):
            raise ValueError(f"当前提醒状态不可标记为已执行：{alert.state.value}")

        if self.is_expired(alert):
            alert.state = AlertState.EXPIRED
            raise ValueError(f"提醒已过有效期：{alert_id}")
        alert.state = AlertState.USER_EXECUTED
        alert.user_executed_at = self._now()
        return alert

    def mark_not_executed(self, alert_id: str) -> AlertPayload:
        """用户标记放弃或未在银行成功下单。"""
        alert = self.get_alert(alert_id)
        if not alert:
            raise KeyError(f"未找到指定的提醒条目：{alert_id}")
        if alert.state not in (AlertState.ALERT_SENT, AlertState.USER_CONFIRMED, AlertState.EXPIRED):
            raise ValueError(f"当前提醒状态不可标记为未执行：{alert.state.value}")
        alert.state = AlertState.NOT_EXECUTED
        return alert

    def check_and_expire_alerts(self, current_time: datetime | None = None) -> list[str]:
        """批量扫描并作废已超期的待处理提醒。"""
        now_dt = current_time or self._now()
        expired_ids: list[str] = []
        for a_id, alert in self._alerts.items():
            if alert.state in (AlertState.ALERT_SENT, AlertState.USER_CONFIRMED):
                if now_dt >= alert.expires_at:
                    alert.state = AlertState.EXPIRED
                    expired_ids.append(a_id)
        return expired_ids

    def is_expired(self, alert: AlertPayload, current_time: datetime | None = None) -> bool:
        """检查单条提醒当前是否已超时作废。"""
        now_dt = current_time or self._now()
        return now_dt >= alert.expires_at

    def get_alert(self, alert_id: str) -> AlertPayload | None:
        """根据唯一标识查询提醒详情。"""
        return self._alerts.get(alert_id)

    def list_alerts(
        self,
        state: AlertState | None = None,
        limit: int = 50,
    ) -> list[AlertPayload]:
        """按时间倒序查询历史提醒列表。"""
        items = list(self._alerts.values())
        if state is not None:
            items = [item for item in items if item.state == state]
        items.sort(key=lambda a: a.created_at, reverse=True)
        return items[:limit]
