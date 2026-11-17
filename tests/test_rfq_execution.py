"""RFQ（报价接受）成交模型单元测试。"""

from datetime import UTC, datetime, timedelta

from src.services.rfq_execution import RFQExecutionEngine
from src.types.domain.execution import RFQRequest
from src.types.domain.product import FeeRule
from src.types.domain.quote import QuoteRaw
from src.types.enums.trading import TradeDirection


def test_rfq_execution_success_with_drift() -> None:
    engine = RFQExecutionEngine(fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.004))
    base_t = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)

    # 构造到达时序：决策时间 10:00，系统延迟后 10:00:02 可成交
    req = RFQRequest(
        request_id="req_01",
        direction=TradeDirection.BUY,
        grams=5.0,
        decision_time=base_t,
        eligible_at=base_t + timedelta(seconds=2),
        is_programmatic=True,
    )

    quotes = [
        QuoteRaw(
            quote_id="q_early",
            source_id="src",
            bid_price=600.0,
            ask_price=600.5,
            quote_at=base_t,  # 在 eligible_at 之前，不满足
            received_at=base_t,
            is_tradable=True,
        ),
        QuoteRaw(
            quote_id="q_eligible",
            source_id="src",
            bid_price=601.0,
            ask_price=601.5,
            quote_at=base_t + timedelta(seconds=3),  # 满足 eligible_at
            received_at=base_t + timedelta(seconds=3),
            is_tradable=True,
        ),
    ]

    fill = engine.simulate_execution(req, quotes)
    assert fill.is_filled is True
    assert fill.filled_grams == 5.0
    # 买入成交价 = base_price (601.5) + slippage
    assert fill.filled_price is not None
    assert fill.filled_price >= 601.5
    assert fill.slippage_cny >= 0.0


def test_rfq_execution_no_eligible_quotes() -> None:
    engine = RFQExecutionEngine()
    base_t = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)

    req = RFQRequest(
        request_id="req_02",
        direction=TradeDirection.SELL,
        grams=2.0,
        decision_time=base_t,
        eligible_at=base_t + timedelta(minutes=5),
        is_programmatic=False,
    )
    # 所有报价都在 eligible_at 之前
    quotes = [
        QuoteRaw(
            quote_id="q1",
            source_id="src",
            bid_price=600.0,
            ask_price=600.5,
            quote_at=base_t,
            received_at=base_t,
            is_tradable=True,
        )
    ]
    fill = engine.simulate_execution(req, quotes)
    assert fill.is_filled is False
    assert fill.rejection_reason == "NO_ELIGIBLE_QUOTES_AFTER_DELAY"
