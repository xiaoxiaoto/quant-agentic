"""决策引擎与独立风控状态机单元测试。"""

from datetime import UTC, datetime

from src.services.decision_engine import DecisionEngine
from src.services.news_risk import NewsRiskEngine
from src.services.risk_engine import RiskEngine
from src.types.domain.account import AccountSnapshot
from src.types.domain.decision import Decision, RiskBudget
from src.types.domain.event import MacroCalendarEvent
from src.types.domain.feature import ModelPrediction
from src.types.domain.product import FeeRule, TradeRule
from src.types.domain.quote import QuoteRaw
from src.types.enums.trading import DataQualityState, DecisionAction, HoldingPeriod, RiskState


def test_decision_data_quality_invalid_abstains() -> None:
    engine = DecisionEngine()
    bad_quote = QuoteRaw(
        quote_id="q_bad",
        source_id="src",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        is_tradable=True,
        quality_state=DataQualityState.STALE,  # 陈旧数据
    )
    snap = AccountSnapshot()
    dec = engine.evaluate_decision(bad_quote, {}, snap)
    assert dec.action == DecisionAction.ABSTAIN
    assert "DATA_QUALITY_FAIL_STALE" in dec.reason_codes


def test_decision_event_lock_abstains() -> None:
    news_eng = NewsRiskEngine()
    now_dt = datetime.now(UTC)
    news_eng.register_macro_event(
        MacroCalendarEvent(
            event_id="fomc_1",
            event_name="FOMC",
            scheduled_at=now_dt,
            lock_window_hours_before=1.0,
            lock_window_hours_after=1.0,
        )
    )
    engine = DecisionEngine(news_engine=news_eng)

    good_quote = QuoteRaw(
        quote_id="q1",
        source_id="src",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=now_dt,
        received_at=now_dt,
        is_tradable=True,
    )
    snap = AccountSnapshot()
    dec = engine.evaluate_decision(good_quote, {}, snap)
    assert dec.action == DecisionAction.ABSTAIN
    assert "EVENT_LOCK" in dec.risk_flags


def test_decision_buy_and_sell_signals() -> None:
    engine = DecisionEngine(edge_threshold=0.002)
    # 周二上午 10:00（非周末休市风险期）
    trade_time = datetime(2026, 3, 3, 10, 0, tzinfo=UTC)
    quote = QuoteRaw(
        quote_id="q_tue",
        source_id="src",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=trade_time,
        received_at=trade_time,
        is_tradable=True,
    )
    snap = AccountSnapshot(cash_available=50000.0, gold_total_g=0.0)

    # 1. 产生超门槛 LCB 预测，触发 BUY
    bull_preds = {
        HoldingPeriod.W1: ModelPrediction(
            horizon=HoldingPeriod.W1,
            expected_net_return=0.015,
            net_return_lcb=0.008,  # > 0.002
            win_probability=0.7,
        )
    }
    dec_buy = engine.evaluate_decision(quote, bull_preds, snap, target_buy_grams=2.0)
    assert dec_buy.action == DecisionAction.BUY
    assert dec_buy.delta_g == 2.0

    # 2. 持仓时预期显著负收益，触发平仓 SELL
    bear_preds = {
        HoldingPeriod.W1: ModelPrediction(
            horizon=HoldingPeriod.W1,
            expected_net_return=-0.015,
            net_return_lcb=-0.025,
            win_probability=0.3,
        )
    }
    snap_holding = AccountSnapshot(cash_available=10000.0, gold_total_g=2.0, gold_available_g=2.0)
    dec_sell = engine.evaluate_decision(quote, bear_preds, snap_holding)
    assert dec_sell.action == DecisionAction.SELL
    assert dec_sell.delta_g == -2.0


def test_risk_gate_veto_rules() -> None:
    risk_eng = RiskEngine(
        risk_budget=RiskBudget(single_trade_net_exit_loss_pct=0.0025),
        trade_rule=TradeRule(buy_min_grams=1.0, liquidate_all_if_le_1g=True),
        fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.004),
    )
    now_dt = datetime.now(UTC)

    # 1. 资金不足被风控一票否决
    dec = Decision(
        decision_id="d1",
        decision_at=now_dt,
        action=DecisionAction.BUY,
        delta_g=10.0,
        target_inventory_g=10.0,
        horizon_primary=HoldingPeriod.W1,
    )
    snap_low_cash = AccountSnapshot(cash_available=500.0)  # 买 10g 需要约 6000 元
    v_dec, _, violations = risk_eng.check_decision(
        dec,
        snap_low_cash,
        current_ask_price=600.0,
        current_bid_price=599.5,
    )
    assert v_dec.action == DecisionAction.ABSTAIN
    assert "INSUFFICIENT_CASH" in violations

    # 2. 碎片库存卖出规则：持仓 <= 1g 必须一次性全卖，若仅卖部分则被一票否决
    dec_sell_part = Decision(
        decision_id="d2",
        decision_at=now_dt,
        action=DecisionAction.SELL,
        delta_g=-0.5,  # 仅卖 0.5g
        target_inventory_g=0.3,
        horizon_primary=HoldingPeriod.W1,
    )
    snap_fragment = AccountSnapshot(gold_total_g=0.8, gold_available_g=0.8)
    v_dec_frag, _, violations_frag = risk_eng.check_decision(
        dec_sell_part,
        snap_fragment,
        current_ask_price=600.0,
        current_bid_price=599.5,
    )
    assert v_dec_frag.action == DecisionAction.ABSTAIN
    assert "FRACTIONAL_INVENTORY_MUST_LIQUIDATE_ALL_LE_1G" in violations_frag

    # 3. 熔断状态阻断任何交易
    v_dec_halt, _, violations_halt = risk_eng.check_decision(
        dec,
        AccountSnapshot(cash_available=100000.0),
        current_ask_price=600.0,
        current_bid_price=599.5,
        risk_state=RiskState.HALT_RECONCILE,
    )
    assert v_dec_halt.action == DecisionAction.ABSTAIN
    assert "HALT_RECONCILE_ACTIVE" in violations_halt
