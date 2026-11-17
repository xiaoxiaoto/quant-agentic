"""影子账本核心业务逻辑测试。"""

import pytest

from src.services.shadow_ledger import ShadowLedger
from src.types.domain.product import FeeRule
from src.types.enums.trading import OrderState, TradeDirection


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
