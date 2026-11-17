"""程序化下单与挂单机制单元测试。"""

from datetime import UTC, datetime

import pytest

from src.services.execution_adapter import ExecutionAdapter
from src.services.shadow_ledger import ShadowLedger
from src.types.domain.execution import RFQFillResult
from src.types.enums.trading import OrderState, TradeDirection


def test_order_lifecycle_submit_ack_and_fill() -> None:
    ledger = ShadowLedger(initial_cash=50000.0)
    adapter = ExecutionAdapter(ledger=ledger)

    # 1. 提交订单
    order = adapter.submit_order(
        client_order_id="cid_101",
        direction=TradeDirection.BUY,
        grams=5.0,
    )
    assert order.state == OrderState.SUBMITTED

    # 2. 银行受理确认
    acked = adapter.acknowledge_order(
        client_order_id="cid_101",
        bank_order_id="bid_901",
    )
    assert acked.state == OrderState.ACKNOWLEDGED
    assert acked.bank_order_id == "bid_901"

    # 3. 成交回报并同步影子账本
    fill_res = RFQFillResult(
        fill_id="f_01",
        request_id="req_01",
        is_filled=True,
        filled_price=600.0,
        filled_grams=5.0,
        fee_cny=0.0,
    )
    filled_order = adapter.process_fill("cid_101", fill_res)
    assert filled_order.state == OrderState.FILLED
    assert filled_order.filled_grams == 5.0

    # 验证影子账本已扣款并增加持仓
    snap = ledger.take_snapshot()
    assert snap.gold_total_g == 5.0
    assert snap.cash_available == 50000.0 - 3000.0


def test_kill_switch_blocks_orders() -> None:
    adapter = ExecutionAdapter()
    adapter.trigger_kill_switch("测试急停")
    assert adapter.is_kill_switch_active() is True

    with pytest.raises(RuntimeError, match="系统处于人工急停状态"):
        adapter.submit_order("cid_fail", TradeDirection.BUY, grams=1.0)

    adapter.reset_kill_switch()
    assert adapter.is_kill_switch_active() is False
    order = adapter.submit_order("cid_ok", TradeDirection.BUY, grams=1.0)
    assert order.state == OrderState.SUBMITTED


def test_batch_order_execution_mechanism() -> None:
    adapter = ExecutionAdapter()
    target_open = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

    # 登记非交易时段挂单：买入 5g，期望最高价 602.0
    inst = adapter.register_batch_instruction(
        instruction_id="inst_01",
        direction=TradeDirection.BUY,
        grams=5.0,
        limit_price=602.0,
        target_open=target_open,
    )
    assert inst.is_executed is False

    # 情景 1：开盘批量价为 601.5 (<= 602.0)，满足买入条件成交
    can_fill_1, res_1 = adapter.execute_batch_at_open("inst_01", bank_batch_price=601.5)
    assert can_fill_1 is True
    assert res_1.is_executed is True

    # 情景 2：开盘批量价为 603.0 (> 602.0)，不满足买入条件未成交
    inst2 = adapter.register_batch_instruction(
        instruction_id="inst_02",
        direction=TradeDirection.BUY,
        grams=5.0,
        limit_price=602.0,
        target_open=target_open,
    )
    can_fill_2, res_2 = adapter.execute_batch_at_open("inst_02", bank_batch_price=603.0)
    assert can_fill_2 is False
    assert res_2.is_executed is False
