"""基准策略评测单元测试。"""

from datetime import UTC, datetime, timedelta

from src.services.baseline import BaselineEngine
from src.types.domain.product import FeeRule
from src.types.domain.quote import QuoteRaw


def test_cash_only() -> None:
    engine = BaselineEngine(initial_cash=100000.0)
    res = engine.run_cash_only([])
    assert res.strategy_name == "CASH_ONLY"
    assert res.total_return_pct == 0.0
    assert res.final_equity == 100000.0
    assert res.total_trades == 0


def test_buy_and_hold_rising_market() -> None:
    # 模拟上涨行情：从 600 上涨至 660（涨幅 10%），卖出手续费 0.4%
    fee_rule = FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.004)
    engine = BaselineEngine(initial_cash=60000.0, fee_rule=fee_rule)

    base_t = datetime(2026, 3, 2, 9, 30, tzinfo=UTC)
    quotes = [
        QuoteRaw(
            quote_id=f"q_{i}",
            source_id="src",
            bid_price=600.0 + i * 6.0,
            ask_price=600.5 + i * 6.0,
            quote_at=base_t + timedelta(days=i),
            received_at=base_t + timedelta(days=i),
            is_tradable=True,
        )
        for i in range(11)
    ]

    res = engine.run_buy_and_hold(quotes)
    assert res.strategy_name == "BUY_AND_HOLD"
    # 买入价为 ask_price 600.5，期末 bid_price 660.0，扣除 0.4% 卖出手续费
    assert res.total_return_pct > 0.08  # 扣费后收益率大于 8%
    assert res.total_trades == 2
    assert res.final_equity > 60000.0


def test_dca_strategy() -> None:
    fee_rule = FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.004)
    engine = BaselineEngine(initial_cash=50000.0, fee_rule=fee_rule)

    base_t = datetime(2026, 3, 2, 9, 30, tzinfo=UTC)
    quotes = [
        QuoteRaw(
            quote_id=f"q_{i}",
            source_id="src",
            bid_price=600.0 + (i % 3) * 5.0,
            ask_price=600.5 + (i % 3) * 5.0,
            quote_at=base_t + timedelta(days=i),
            received_at=base_t + timedelta(days=i),
            is_tradable=True,
        )
        for i in range(15)
    ]

    res = engine.run_dca(quotes, step_intervals=3)
    assert res.strategy_name == "DCA"
    assert res.total_trades > 2
    assert res.final_equity > 0.0


def test_compare_all() -> None:
    engine = BaselineEngine(initial_cash=50000.0)
    base_t = datetime(2026, 3, 2, 9, 30, tzinfo=UTC)
    quotes = [
        QuoteRaw(
            quote_id=f"q_{i}",
            source_id="src",
            bid_price=600.0 + i * 2.0,
            ask_price=600.5 + i * 2.0,
            quote_at=base_t + timedelta(days=i),
            received_at=base_t + timedelta(days=i),
            is_tradable=True,
        )
        for i in range(10)
    ]

    report = engine.compare_all(quotes)
    assert len(report.metrics) == 3
    strat_names = [m.strategy_name for m in report.metrics]
    assert "CASH_ONLY" in strat_names
    assert "BUY_AND_HOLD" in strat_names
    assert "DCA" in strat_names
    assert report.best_strategy in strat_names
