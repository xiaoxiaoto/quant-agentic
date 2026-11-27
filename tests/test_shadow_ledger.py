"""影子账本核心业务逻辑测试。"""

from typing import cast

import pytest

from src.contracts.domains.product import FeeRule
from src.contracts.enums.trading import OrderState, TradeDirection
from src.services.execution.shadow_ledger import ShadowLedger


def test_shadow_ledger_initial_state() -> None:
    ledger = ShadowLedger(initial_cash=50000.0)
    snap = ledger.take_snapshot()
    assert snap.cash_available == 50000.0
    assert snap.gold_total_g == 0.0
    assert snap.cost_basis_per_g is None
    assert snap.realized_pnl == 0.0
    assert snap.unrealized_pnl == 0.0


def test_shadow_ledger_buy_success() -> None:
    # 买入 10g，价格 600 元/g，买入手续费率为 0%
    ledger = ShadowLedger(initial_cash=50000.0, fee_rule=FeeRule(buy_fee_rate=0.0))
    event = ledger.record_buy(
        client_order_id="order_buy_1",
        grams=10.0,
        price=600.0,
    )
    assert event.state == OrderState.FILLED
    assert event.direction == TradeDirection.BUY
    assert event.filled_grams == 10.0

    snap = ledger.take_snapshot()
    assert snap.gold_total_g == 10.0
    assert snap.gold_available_g == 10.0
    assert snap.cash_available == 50000.0 - 6000.0
    assert snap.cost_basis_per_g == 600.0


def test_shadow_ledger_buy_insufficient_cash() -> None:
    ledger = ShadowLedger(initial_cash=1000.0)
    with pytest.raises(ValueError, match="可用资金不足"):
        ledger.record_buy(
            client_order_id="order_fail",
            grams=10.0,
            price=600.0,
        )


def test_shadow_ledger_cost_basis_dilution() -> None:
    # 两次买入不同价格，验证加权平均持仓成本
    ledger = ShadowLedger(initial_cash=50000.0)
    ledger.record_buy("b1", grams=10.0, price=600.0)  # 6000 元
    ledger.record_buy("b2", grams=10.0, price=620.0)  # 6200 元
    snap = ledger.take_snapshot()
    assert snap.gold_total_g == 20.0
    # 加权成本 = (6000 + 6200) / 20 = 610.0
    assert snap.cost_basis_per_g == 610.0


def test_shadow_ledger_sell_success_and_realized_pnl() -> None:
    # 买入 10g @ 600，卖出 5g @ 620，卖出手续费 0.4%
    ledger = ShadowLedger(
        initial_cash=50000.0,
        fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.004),
    )
    ledger.record_buy("b1", grams=10.0, price=600.0)

    # 卖出 5g @ 620，毛收入 3100，手续费 3100 * 0.004 = 12.4，净收入 3087.6
    # 成本 5 * 600 = 3000，已实现盈亏 = 3087.6 - 3000 = 87.6
    event = ledger.record_sell("s1", grams=5.0, price=620.0)
    assert event.state == OrderState.FILLED
    assert event.direction == TradeDirection.SELL

    snap = ledger.take_snapshot()
    assert snap.gold_total_g == 5.0
    assert snap.cost_basis_per_g == 600.0  # 剩余部分成本单价保持 600
    assert round(snap.realized_pnl, 2) == 87.60
    assert round(snap.cash_available, 2) == 50000.0 - 6000.0 + 3087.60


def test_shadow_ledger_sell_insufficient_gold() -> None:
    ledger = ShadowLedger(initial_cash=50000.0)
    ledger.record_buy("b1", grams=5.0, price=600.0)
    with pytest.raises(ValueError, match="可用黄金克重不足"):
        ledger.record_sell("s1", grams=10.0, price=610.0)


def test_shadow_ledger_mark_to_market() -> None:
    # 持仓 10g @ 600，当前买价 610，卖出手续费 0.4%
    ledger = ShadowLedger(
        initial_cash=50000.0,
        fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.004),
    )
    ledger.record_buy("b1", grams=10.0, price=600.0)

    # 重估浮盈：10 * 610 * (1 - 0.004) - 6000 = 6075.6 - 6000 = 75.60
    unrealized = ledger.mark_to_market(current_bid_price=610.0)
    assert float(unrealized) == 75.60

    total_eq = ledger.get_total_equity(current_bid_price=610.0)
    # 总资产 = 现金 44000 + 黄金市值 10 * 610 = 50100.0
    assert total_eq == 50100.0


def test_ledger_replay_validation_and_buy_fee_cost() -> None:
    ledger = ShadowLedger(initial_cash=1000.0, fee_rule=FeeRule(buy_fee_rate=0.01, sell_fee_rate=0))
    ledger.record_buy("buy", 1, 100)
    ledger.record_buy("buy", 1, 100)
    assert ledger.take_snapshot().gold_total_g == 1
    with pytest.raises(ValueError):
        ledger.record_buy("negative", -1, 100)
    with pytest.raises(ValueError):
        ledger.record_buy("buy", 2, 100)
    ledger.record_sell("sell", 1, 100)
    assert ledger.take_snapshot().realized_pnl == -1


@pytest.mark.parametrize("grams", [0.00001, 1.00001])
def test_ledger_rejects_unsupported_gold_precision(grams: float) -> None:
    ledger = ShadowLedger()
    with pytest.raises(ValueError):
        ledger.record_buy("tiny", grams, 1000)
    assert ledger.take_snapshot().gold_total_g == 0


def test_concurrent_ledger_replay_books_once() -> None:
    from concurrent.futures import ThreadPoolExecutor
    from decimal import Decimal
    from threading import Barrier, BrokenBarrierError
    from unittest.mock import patch

    ledger = ShadowLedger()
    barrier = Barrier(2)
    original = ledger._existing_trade

    def synchronized_lookup(
        client_order_id: str,
        direction: TradeDirection,
        grams: float,
        price: float,
        fee: Decimal,
    ) -> object:
        result = original(client_order_id, direction, grams, price, fee)
        try:
            barrier.wait(timeout=0.1)
        except BrokenBarrierError:
            pass
        return result

    with patch.object(ledger, "_existing_trade", side_effect=synchronized_lookup):
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda _: ledger.record_buy("same", 1, 100), range(2)))
    assert ledger.take_snapshot().gold_total_g == 1
    assert len(ledger.get_order_history()) == 1


def test_reset_and_seed_holdings(tmp_path: object) -> None:
    from pathlib import Path

    from src.repositories.runtime_state import RuntimeStateRepository

    db_path = Path(str(tmp_path)) / "quant-agentic.sqlite3"
    store = RuntimeStateRepository(db_path)
    ledger = ShadowLedger(initial_cash=50000.0, state_store=store)

    # 先产生一笔旧买入流水
    ledger.record_buy("old_buy_1", 2.0, 500.0)
    assert ledger.take_snapshot().gold_total_g == 2.0

    # 按京东持仓重置并初始化：CMBC 10g @ 600, ICBC 5g @ 620
    # 总成本 = 6000 + 3100 = 9100
    # 可用现金 = 100000 - 9100 = 90900
    snap = ledger.reset_and_seed_holdings(
        initial_cash=100000.0,
        holdings=[("CMBC", 10.0, 600.0), ("ICBC", 5.0, 620.0)],
    )
    assert snap.gold_total_g == 15.0
    assert snap.gold_available_g == 15.0
    assert snap.cash_available == 90900.0
    assert snap.cost_basis_per_g is not None
    assert round(snap.cost_basis_per_g, 2) == round(9100.0 / 15.0, 2)
    assert len(ledger.get_order_history()) == 2

    # 重启影子账本恢复验证：旧数据已被清空，仅保留种子持仓
    reloaded = ShadowLedger(initial_cash=100000.0, state_store=RuntimeStateRepository(db_path))
    reloaded_snap = reloaded.take_snapshot()
    assert reloaded_snap.gold_total_g == 15.0
    assert reloaded_snap.cash_available == 90900.0

    # 成本超过总资金时抛出异常
    with pytest.raises(ValueError, match="超过初始资金"):
        ledger.reset_and_seed_holdings(
            initial_cash=5000.0,
            holdings=[("CMBC", 10.0, 600.0)],
        )


def test_compute_fifo_lots() -> None:
    ledger = ShadowLedger(initial_cash=100000.0)
    # 批次 1: 买入 10g @ 500
    ledger.record_buy("buy_1", 10.0, 500.0)
    # 批次 2: 买入 5g @ 600
    ledger.record_buy("buy_2", 5.0, 600.0)
    # 批次 3: 卖出 12g @ 700 (应撮合掉全部 buy_1 的 10g，以及 buy_2 的 2g)
    ledger.record_sell("sell_1", 12.0, 700.0)

    # 重估金价 750
    res = ledger.compute_fifo_lots(reference_price=750.0)
    assert res.total_orders == 3
    assert res.open_lot_count == 1
    assert res.open_grams == 3.0
    # 剩余 3g buy_2，买入价 600，在持成本 = 1800.0
    assert res.open_position_cost == 1800.0

    items = res.items
    # 倒序排列，第一个是 sell_1
    assert items[0].client_order_id == "sell_1"
    assert items[0].lot_status == "SELL"

    # 第二个是 buy_2，状态 PARTIAL，剩余 3g，浮盈 (750 - 600) * 3 = 450.0
    assert items[1].client_order_id == "buy_2"
    assert items[1].lot_status == "PARTIAL"
    assert items[1].remaining_grams == 3.0
    assert items[1].lot_pnl == 450.0

    # 第三个是 buy_1，状态 CLOSED，剩余 0g
    assert items[2].client_order_id == "buy_1"
    assert items[2].lot_status == "CLOSED"
    assert items[2].remaining_grams == 0.0

    # 仅查看在持批次
    only_open_res = ledger.compute_fifo_lots(reference_price=750.0, only_open=True)
    assert len(only_open_res.items) == 1
    assert only_open_res.items[0].client_order_id == "buy_2"


def test_compute_fifo_lots_fee_allocation_and_pagination_bounds() -> None:
    """验证 FIFO 批次按剩余比例分摊买入手续费，并严格校验分页边界。"""
    ledger = ShadowLedger(initial_cash=100000.0, fee_rule=FeeRule(buy_fee_rate=0.01, sell_fee_rate=0.0))
    # 买入 10g @ 500，买入手续费 50.0
    ledger.record_buy("buy_fee", 10.0, 500.0)
    # 卖出 4g @ 600，剩余 6g 应分摊 30.0 手续费，在持成本 = 3000 + 30 = 3030.0
    ledger.record_sell("sell_part", 4.0, 600.0)

    res = ledger.compute_fifo_lots(reference_price=600.0, only_open=True)
    assert res.open_grams == 6.0
    assert res.open_position_cost == 3030.0
    assert res.items[0].lot_pnl == 570.0  # 6 * 600 - 3030

    # limit == 0 必须返回空列表
    assert ledger.compute_fifo_lots(limit=0).items == []

    # 负 offset / 负 limit 抛 ValueError
    with pytest.raises(ValueError, match="offset"):
        ledger.compute_fifo_lots(offset=-1)
    with pytest.raises(ValueError, match="limit"):
        ledger.compute_fifo_lots(limit=-1)
