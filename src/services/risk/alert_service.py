"""交易提醒与人工确认交互流转服务。

实现基于决策输出向用户生成规范合规提醒，并管理以下状态生命周期：
INTENT_CREATED -> RISK_REJECTED / ALERT_SENT -> EXPIRED / USER_CONFIRMED / USER_DECLINED
-> USER_EXECUTED / NOT_EXECUTED -> RECONCILED / DISCREPANCY
"""

import uuid
from datetime import UTC, datetime, timedelta
from threading import RLock

from src.contracts.domains.account import AccountSnapshot
from src.contracts.domains.alert import AlertPayload
from src.contracts.domains.decision import Decision
from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import AlertState, DecisionAction
from src.repositories.runtime_state import RuntimeStateRepository
from src.services.decision.cost_calculator import CostCalculator


class AlertService:
    """提醒与人工确认流程管理服务。"""

    _MAX_CACHED_ALERTS: int = 500

    def __init__(
        self,
        default_validity_minutes: float = 15.0,
        cost_calculator: CostCalculator | None = None,
        fee_rule: FeeRule | None = None,
        state_store: RuntimeStateRepository | None = None,
    ) -> None:
        self._lock = RLock()
        self._state_store = state_store
        self._validity_m: float = default_validity_minutes
        self._calculator: CostCalculator = cost_calculator or CostCalculator(fee_rule)
        self._alerts: dict[str, AlertPayload] = {}
        self._alerts_by_decision: dict[str, str] = {}
        if self._state_store is not None:
            for item in self._state_store.load_alerts(limit=self._MAX_CACHED_ALERTS):
                self._alerts[item.alert_id] = item
                self._alerts_by_decision[item.decision_id] = item.alert_id

    def _prune_cache(self) -> None:
        """淘汰超限的已终结历史提醒，保留全部活跃待处理提醒并限制内存占用。"""
        if len(self._alerts) <= self._MAX_CACHED_ALERTS:
            return
        active_states = {AlertState.ALERT_SENT, AlertState.USER_CONFIRMED}
        inactive = sorted(
            (item for item in self._alerts.values() if item.state not in active_states),
            key=lambda a: a.created_at,
        )
        overflow = len(self._alerts) - self._MAX_CACHED_ALERTS
        for item in inactive[:overflow]:
            self._alerts.pop(item.alert_id, None)
            if self._alerts_by_decision.get(item.decision_id) == item.alert_id:
                self._alerts_by_decision.pop(item.decision_id, None)

    def _persist_alert(self, alert: AlertPayload) -> None:
        if self._state_store is not None:
            self._state_store.upsert_alert(alert)
        self._prune_cache()

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
        *,
        is_reference_estimated: bool = False,
    ) -> AlertPayload | None:
        """根据风控审核通过的决策建议生成对外提醒。

        若决策为观望（HOLD）或弃权（ABSTAIN）则不生成交易提醒。
        """
        if decision.action not in (DecisionAction.BUY, DecisionAction.SELL):
            return None

        with self._lock:
            existing_id = self._alerts_by_decision.get(decision.decision_id)
            if existing_id is not None and existing_id in self._alerts:
                return self._alerts[existing_id].model_copy(deep=True)

            now_dt = self._now()
            val_m = self._validity_m if validity_minutes is None else validity_minutes
            expires_at = now_dt + timedelta(minutes=val_m)
            if decision.expires_at is not None:
                dec_exp = (
                    decision.expires_at.astimezone(UTC)
                    if decision.expires_at.tzinfo is not None
                    else decision.expires_at.replace(tzinfo=UTC)
                )
                expires_at = min(expires_at, dec_exp)
            alert_id = f"alt_{uuid.uuid4().hex[:10]}"

            spread = round(quote.ask_price - quote.bid_price, 4)
            break_even = self._compute_break_even_price(decision.action, quote.ask_price, quote.bid_price)
            net_loss = self._compute_net_exit_loss(quote.ask_price, spread)
            initial_state = AlertState.EXPIRED if expires_at <= now_dt else AlertState.ALERT_SENT
            risk_flags = list(decision.risk_flags)
            if is_reference_estimated and "REFERENCE_ESTIMATED_QUOTE" not in risk_flags:
                risk_flags.append("REFERENCE_ESTIMATED_QUOTE")
            disclaimer = (
                "该信号基于单边参考价与估算点差生成，非投资建议，请以银行官方实时双边报价为准"
                if is_reference_estimated
                else "该信号为研究输出，非投资建议"
            )

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
                risk_flags=risk_flags,
                state=initial_state,
                disclaimer=disclaimer,
                is_reference_estimated=is_reference_estimated,
            )
            self._persist_alert(payload)
            self._alerts[alert_id] = payload
            self._alerts_by_decision[decision.decision_id] = alert_id
            return payload

    def confirm_alert(self, alert_id: str, *, current_time: datetime | None = None) -> AlertPayload:
        """用户确认交易意向。"""
        now_dt = current_time or self._now()
        with self._lock:
            alert = self._alerts.get(alert_id)
            if not alert:
                raise KeyError(f"未找到指定的提醒条目：{alert_id}")
            if alert.state not in (AlertState.ALERT_SENT, AlertState.USER_CONFIRMED):
                raise ValueError(f"当前提醒状态不可确认为意向：{alert.state.value}")
            if self.is_expired(alert, now_dt):
                prev_state = alert.state
                alert.state = AlertState.EXPIRED
                try:
                    self._persist_alert(alert)
                except Exception:
                    alert.state = prev_state
                    raise
                raise ValueError(f"提醒已过有效期：{alert_id}")
            if alert.state != AlertState.ALERT_SENT:
                raise ValueError(f"当前提醒状态不可确认为意向：{alert.state.value}")

            prev_state = alert.state
            prev_confirmed = alert.user_confirmed_at
            alert.state = AlertState.USER_CONFIRMED
            alert.user_confirmed_at = now_dt
            try:
                self._persist_alert(alert)
            except Exception:
                alert.state = prev_state
                alert.user_confirmed_at = prev_confirmed
                raise
            return alert

    def decline_alert(self, alert_id: str, *, current_time: datetime | None = None) -> AlertPayload:
        """用户拒绝本次交易建议。"""
        now_dt = current_time or self._now()
        with self._lock:
            alert = self._alerts.get(alert_id)
            if not alert:
                raise KeyError(f"未找到指定的提醒条目：{alert_id}")
            if alert.state not in (AlertState.ALERT_SENT, AlertState.USER_CONFIRMED):
                raise ValueError(f"当前提醒状态不可拒绝建议：{alert.state.value}")
            if self.is_expired(alert, now_dt):
                prev_state = alert.state
                alert.state = AlertState.EXPIRED
                try:
                    self._persist_alert(alert)
                except Exception:
                    alert.state = prev_state
                    raise
                raise ValueError(f"提醒已过有效期：{alert_id}")
            prev_state = alert.state
            alert.state = AlertState.USER_DECLINED
            try:
                self._persist_alert(alert)
            except Exception:
                alert.state = prev_state
                raise
            return alert

    def mark_executed(self, alert_id: str, *, current_time: datetime | None = None) -> AlertPayload:
        """用户标记已在银行界面完成实际下单。"""
        now_dt = current_time or self._now()
        with self._lock:
            alert = self._alerts.get(alert_id)
            if not alert:
                raise KeyError(f"未找到指定的提醒条目：{alert_id}")
            if alert.state not in (AlertState.ALERT_SENT, AlertState.USER_CONFIRMED):
                raise ValueError(f"当前提醒状态不可标记为已执行：{alert.state.value}")

            if self.is_expired(alert, now_dt):
                prev_state = alert.state
                alert.state = AlertState.EXPIRED
                try:
                    self._persist_alert(alert)
                except Exception:
                    alert.state = prev_state
                    raise
                raise ValueError(f"提醒已过有效期：{alert_id}")
            prev_state = alert.state
            prev_executed = alert.user_executed_at
            alert.state = AlertState.USER_EXECUTED
            alert.user_executed_at = now_dt
            try:
                self._persist_alert(alert)
            except Exception:
                alert.state = prev_state
                alert.user_executed_at = prev_executed
                raise
            return alert

    def mark_not_executed(self, alert_id: str) -> AlertPayload:
        """用户标记放弃或未在银行成功下单。"""
        with self._lock:
            alert = self._alerts.get(alert_id)
            if not alert:
                raise KeyError(f"未找到指定的提醒条目：{alert_id}")
            if alert.state not in (AlertState.ALERT_SENT, AlertState.USER_CONFIRMED, AlertState.EXPIRED):
                raise ValueError(f"当前提醒状态不可标记为未执行：{alert.state.value}")
            prev_state = alert.state
            alert.state = AlertState.NOT_EXECUTED
            try:
                self._persist_alert(alert)
            except Exception:
                alert.state = prev_state
                raise
            return alert

    def check_and_expire_alerts(self, current_time: datetime | None = None) -> list[str]:
        """批量扫描并作废已超期的待处理提醒，并在单次写事务中同步持久化状态变更。"""
        now_dt = current_time or self._now()
        with self._lock:
            expired: list[tuple[AlertPayload, AlertState]] = []
            for alert in self._alerts.values():
                if alert.state in (AlertState.ALERT_SENT, AlertState.USER_CONFIRMED):
                    if self.is_expired(alert, now_dt):
                        expired.append((alert, alert.state))
            if not expired:
                return []
            for alert, _prev_state in expired:
                alert.state = AlertState.EXPIRED
            if self._state_store is not None:
                try:
                    self._state_store.save_alerts([alert for alert, _ in expired])
                except Exception:
                    for alert, prev_state in expired:
                        alert.state = prev_state
                    raise
            return [alert.alert_id for alert, _ in expired]

    def is_expired(self, alert: AlertPayload, current_time: datetime | None = None) -> bool:
        """检查单条提醒当前是否已超时作废。"""
        now_dt = current_time or self._now()
        now_utc = now_dt.astimezone(UTC) if now_dt.tzinfo is not None else now_dt.replace(tzinfo=UTC)
        exp_utc = (
            alert.expires_at.astimezone(UTC)
            if alert.expires_at.tzinfo is not None
            else alert.expires_at.replace(tzinfo=UTC)
        )
        return now_utc >= exp_utc

    def get_alert(self, alert_id: str) -> AlertPayload | None:
        """根据唯一标识查询提醒详情。"""
        with self._lock:
            return self._alerts.get(alert_id)

    def list_alerts(
        self,
        state: AlertState | None = None,
        limit: int = 50,
    ) -> list[AlertPayload]:
        """按时间倒序查询历史提醒列表。"""
        with self._lock:
            items = list(self._alerts.values())
        if state is not None:
            items = [item for item in items if item.state == state]
        items.sort(key=lambda a: a.created_at, reverse=True)
        return items[:limit]
