"""提醒与人工确认流程管理单元测试。"""

from datetime import UTC, datetime, timedelta

import pytest

from src.contracts.domains.account import AccountSnapshot
from src.contracts.domains.decision import Decision
from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import AlertState, DecisionAction, HoldingPeriod
from src.services.risk.alert_service import AlertService


def test_create_alert_payload_content() -> None:
    service = AlertService(default_validity_minutes=15.0, fee_rule=FeeRule(sell_fee_rate=0.004))
    now_dt = datetime.now(UTC)

    decision = Decision(
        decision_id="dec_01",
        decision_at=now_dt,
        action=DecisionAction.BUY,
        delta_g=2.0,
        target_inventory_g=2.0,
        horizon_primary=HoldingPeriod.W1,
        reason_codes=["MOMENTUM_LCB_BREAKOUT"],
        risk_flags=["NONE"],
    )
    quote = QuoteRaw(
        quote_id="q1",
        source_id="src",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=now_dt,
        received_at=now_dt,
        is_tradable=True,
    )
    snap = AccountSnapshot(cash_available=50000.0)

    alert = service.create_alert(decision, quote, snap)
    assert alert is not None
    assert alert.action == DecisionAction.BUY
    assert alert.suggested_grams == 2.0
    assert alert.spread == 0.5
    # 保本价 = 600.5 / (1 - 0.004) = 602.9116
    assert alert.break_even_price > 602.9
    assert alert.net_exit_loss_cny > 0.0
    assert alert.state == AlertState.ALERT_SENT
    assert alert.disclaimer == "该信号为研究输出，非投资建议"


def test_alert_state_transitions() -> None:
    service = AlertService()
    now_dt = datetime.now(UTC)

    decision = Decision(
        decision_id="dec_02",
        decision_at=now_dt,
        action=DecisionAction.BUY,
        delta_g=1.0,
        target_inventory_g=1.0,
        horizon_primary=HoldingPeriod.W1,
    )
    quote = QuoteRaw(
        quote_id="q2",
        source_id="src",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=now_dt,
        received_at=now_dt,
        is_tradable=True,
    )
    alert = service.create_alert(decision, quote, AccountSnapshot())
    assert alert is not None
    alert_id = alert.alert_id

    # 1. 用户确认意图
    confirmed = service.confirm_alert(alert_id)
    assert confirmed.state == AlertState.USER_CONFIRMED
    assert confirmed.user_confirmed_at is not None

    # 2. 用户在银行执行成交
    executed = service.mark_executed(alert_id)
    assert executed.state == AlertState.USER_EXECUTED
    assert executed.user_executed_at is not None


def test_alert_expiration() -> None:
    service = AlertService(default_validity_minutes=10.0)
    now_dt = datetime.now(UTC)

    decision = Decision(
        decision_id="dec_03",
        decision_at=now_dt,
        action=DecisionAction.BUY,
        delta_g=1.0,
        target_inventory_g=1.0,
        horizon_primary=HoldingPeriod.W1,
    )
    quote = QuoteRaw(
        quote_id="q3",
        source_id="src",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=now_dt,
        received_at=now_dt,
        is_tradable=True,
    )
    alert = service.create_alert(decision, quote, AccountSnapshot(), validity_minutes=5.0)
    assert alert is not None

    # 模拟 10 分钟后检查过期
    future_t = now_dt + timedelta(minutes=10)
    expired_ids = service.check_and_expire_alerts(future_t)
    assert alert.alert_id in expired_ids
    assert alert.state == AlertState.EXPIRED

    # 已超期的提醒无法再确认
    with pytest.raises(ValueError, match="当前提醒状态不可确认为意向"):
        service.confirm_alert(alert.alert_id)


@pytest.mark.parametrize("operation", ["mark_executed", "decline_alert", "mark_not_executed"])
def test_alert_terminal_state_cannot_be_overwritten(operation: str) -> None:
    service = AlertService()
    now = datetime.now(UTC)
    decision = Decision(
        decision_id="terminal",
        decision_at=now,
        action=DecisionAction.BUY,
        delta_g=1.0,
        target_inventory_g=1.0,
        horizon_primary=HoldingPeriod.W1,
    )
    quote = QuoteRaw(
        quote_id="q", source_id="src", bid_price=600.0, ask_price=600.5, quote_at=now, received_at=now, is_tradable=True
    )
    alert = service.create_alert(decision, quote, AccountSnapshot())
    assert alert is not None
    service.mark_executed(alert.alert_id)
    with pytest.raises(ValueError):
        getattr(service, operation)(alert.alert_id)
    assert alert.state == AlertState.USER_EXECUTED


def test_alert_uses_shared_fee_calculator_and_decision_expiry() -> None:
    from src.services.decision.cost_calculator import CostCalculator

    now = datetime.now(UTC)
    calc = CostCalculator(FeeRule(buy_fee_rate=0.002, sell_fee_rate=0.01))
    service = AlertService(cost_calculator=calc)
    decision = Decision(
        decision_id="fees",
        decision_at=now,
        action=DecisionAction.BUY,
        delta_g=1.0,
        target_inventory_g=1.0,
        horizon_primary=HoldingPeriod.W1,
        expires_at=now + timedelta(minutes=1),
    )
    quote = QuoteRaw(
        quote_id="q", source_id="src", bid_price=600.0, ask_price=600.5, quote_at=now, received_at=now, is_tradable=True
    )
    alert = service.create_alert(decision, quote, AccountSnapshot())
    assert alert is not None
    assert alert.break_even_price == round(calc.break_even_bid(600.5), 4)
    assert alert.expires_at == decision.expires_at
    alert.expires_at = now - timedelta(seconds=1)
    with pytest.raises(ValueError):
        service.mark_executed(alert.alert_id)
    assert alert.state == AlertState.EXPIRED
