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
