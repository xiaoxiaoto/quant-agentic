"""影子运行全链路仿真单元测试。"""

from datetime import UTC, datetime, timedelta

from src.services.shadow_runner import ShadowRunner
from src.types.domain.product import FeeRule
from src.types.domain.quote import QuoteRaw


def test_shadow_runner_full_pipeline() -> None:
    runner = ShadowRunner(initial_cash=50000.0, fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.004))
    base_t = datetime(2026, 3, 2, 9, 30, tzinfo=UTC)

    # 构造模拟连续行情序列
    quotes = [
        QuoteRaw(
            quote_id=f"q_{i}",
            source_id="src",
            bid_price=600.0 + (i % 5) * 2.0,
            ask_price=600.5 + (i % 5) * 2.0,
            quote_at=base_t + timedelta(hours=i * 4),
            received_at=base_t + timedelta(hours=i * 4),
            is_tradable=True,
        )
        for i in range(25)
    ]

    report = runner.run_shadow_simulation(quotes, trade_grams_per_order=1.0)
    assert report.run_id.startswith("shadow_")
    assert report.initial_cash == 50000.0
    assert report.final_equity > 0.0
    assert report.max_drawdown_pct >= 0.0
    assert report.summary != ""


def test_shadow_runner_counts_closed_trade_pnl_and_initial_drawdown() -> None:
    from unittest.mock import MagicMock

    from src.services.decision_engine import DecisionEngine
    from src.services.rfq_execution import RFQExecutionEngine
    from src.types.domain.decision import Decision
    from src.types.enums.trading import DecisionAction, HoldingPeriod

    base = datetime(2026, 3, 2, 10, tzinfo=UTC)
    prices = [100, 110, 100, 95]
    quotes = [
        QuoteRaw(
            quote_id=str(i),
            source_id="s",
            bid_price=price,
            ask_price=price,
            quote_at=base + timedelta(hours=i),
            received_at=base + timedelta(hours=i),
            is_tradable=True,
        )
        for i, price in enumerate(prices)
    ]
    decision = MagicMock(spec=DecisionEngine)
    decision.evaluate_decision.side_effect = [
        Decision(
            decision_id=str(i),
            decision_at=q.quote_at,
            action=action,
            delta_g=grams,
            target_inventory_g=max(grams, 0),
            horizon_primary=HoldingPeriod.W1,
        )
        for i, (q, action, grams) in enumerate(
            zip(
                quotes,
                [DecisionAction.BUY, DecisionAction.SELL, DecisionAction.BUY, DecisionAction.SELL],
                [1, -1, 1, -1],
                strict=True,
            )
        )
    ]
    rfq = MagicMock(wraps=RFQExecutionEngine(fee_rule=FeeRule(sell_fee_rate=0)))
    report = ShadowRunner(
        initial_cash=1000, decision_engine=decision, rfq_engine=rfq, fee_rule=FeeRule(sell_fee_rate=0)
    ).run_shadow_simulation(quotes)
    assert report.winning_trades == 1
    assert report.win_rate == 0.5
    assert report.max_drawdown_pct > 0
    assert all(len(call.kwargs["candidate_quotes"]) == 1 for call in rfq.simulate_execution.call_args_list)


def test_shadow_runner_uses_visibility_time_and_omits_uncomputed_dsr() -> None:
    from unittest.mock import MagicMock

    from src.services.decision_engine import DecisionEngine
    from src.types.domain.decision import Decision
    from src.types.enums.trading import DecisionAction, HoldingPeriod

    base = datetime(2026, 3, 2, 10, tzinfo=UTC)
    delayed = QuoteRaw(
        quote_id="delayed",
        source_id="s",
        bid_price=100,
        ask_price=100,
        quote_at=base,
        received_at=base + timedelta(hours=2),
        is_tradable=True,
    )
    earlier = QuoteRaw(
        quote_id="visible-first",
        source_id="s",
        bid_price=100,
        ask_price=100,
        quote_at=base + timedelta(hours=1),
        received_at=base + timedelta(hours=1),
        is_tradable=True,
    )
    engine = MagicMock(spec=DecisionEngine)
    engine.evaluate_decision.return_value = Decision(
        decision_id="hold",
        decision_at=base,
        action=DecisionAction.HOLD,
        delta_g=0,
        target_inventory_g=0,
        horizon_primary=HoldingPeriod.W1,
    )
    report = ShadowRunner(decision_engine=engine).run_shadow_simulation([delayed, earlier])
    calls = engine.evaluate_decision.call_args_list
    assert [call.kwargs["quote"].quote_id for call in calls] == ["visible-first", "delayed"]
    assert calls[-1].kwargs["account"].as_of == delayed.received_at
    assert report.start_time == earlier.received_at
    assert report.end_time == delayed.received_at
    assert report.deflated_sharpe is None


def test_shadow_equity_includes_remaining_position_exit_fee() -> None:
    from unittest.mock import MagicMock

    from src.services.decision_engine import DecisionEngine
    from src.services.rfq_execution import RFQExecutionEngine
    from src.types.domain.decision import Decision
    from src.types.enums.trading import DecisionAction, HoldingPeriod

    now = datetime(2026, 3, 2, 10, tzinfo=UTC)
    quote = QuoteRaw(
        quote_id="buy-only",
        source_id="s",
        bid_price=100,
        ask_price=100,
        quote_at=now,
        received_at=now,
        is_tradable=True,
    )
    decision = MagicMock(spec=DecisionEngine)
    decision.evaluate_decision.return_value = Decision(
        decision_id="buy",
        decision_at=now,
        action=DecisionAction.BUY,
        delta_g=1,
        target_inventory_g=1,
        horizon_primary=HoldingPeriod.W1,
    )
    fee = FeeRule(sell_fee_rate=0.01)
    rfq = RFQExecutionEngine(fee_rule=fee, prog_delay_drift_rate=0)
    report = ShadowRunner(
        initial_cash=1000, fee_rule=fee, decision_engine=decision, rfq_engine=rfq
    ).run_shadow_simulation([quote])
    assert report.final_equity == 999.0
    assert report.total_return_pct == -0.001
    assert report.max_drawdown_pct == 0.001
