"""采集器质量与周末跳空统计单元测试。"""

from datetime import UTC, datetime, timedelta

from src.services.collector_quality import CollectorQualityAnalyzer
from src.types.domain.quote import QuoteRaw


def test_collector_quality_empty() -> None:
    analyzer = CollectorQualityAnalyzer()
    rep = analyzer.analyze([])
    assert rep.total_records == 0
    assert rep.gap_count == 0


def test_collector_quality_latency_and_spread() -> None:
    analyzer = CollectorQualityAnalyzer()
    base_t = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)

    quotes = [
        QuoteRaw(
            quote_id=f"q_{i}",
            source_id="src",
            bid_price=600.0,
            ask_price=600.6,  # spread = 0.6
            quote_at=base_t + timedelta(minutes=i),
            received_at=base_t + timedelta(minutes=i, milliseconds=120),  # 120ms 延迟
            is_tradable=True,
        )
        for i in range(10)
    ]

    rep = analyzer.analyze(quotes)
    assert rep.total_records == 10
    assert rep.avg_latency_ms == 120.0
    assert rep.spread_mean == 0.6


def test_collector_quality_weekend_gap_detection() -> None:
    analyzer = CollectorQualityAnalyzer()
    # 模拟周六凌晨 02:30 (收盘) 与周一上午 09:00 (开盘)
    sat_close = datetime(2026, 3, 7, 2, 30, tzinfo=UTC)
    mon_open = datetime(2026, 3, 9, 9, 0, tzinfo=UTC)

    q_close = QuoteRaw(
        quote_id="q_sat",
        source_id="src",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=sat_close,
        received_at=sat_close,
        is_tradable=True,
    )
    q_open = QuoteRaw(
        quote_id="q_mon",
        source_id="src",
        bid_price=612.0,
        ask_price=612.5,
        quote_at=mon_open,
        received_at=mon_open,
        is_tradable=True,
    )

    rep = analyzer.analyze([q_close, q_open])
    assert len(rep.weekend_gaps) == 1
    w_gap = rep.weekend_gaps[0]
    assert w_gap.gap_amount == 12.0  # 612.25 - 600.25 = 12.0
    assert w_gap.gap_pct > 0.019
    assert rep.max_weekend_gap_pct > 0.019


def test_normal_weekend_closure_is_not_collection_outage() -> None:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    times = [datetime(2026, 3, 7, 2, 29, tzinfo=tz), datetime(2026, 3, 9, 9, tzinfo=tz)]
    quotes = [
        QuoteRaw(
            quote_id=str(i), source_id="s", bid_price=600, ask_price=601, quote_at=t, received_at=t, is_tradable=True
        )
        for i, t in enumerate(times)
    ]
    assert CollectorQualityAnalyzer().analyze(quotes).gap_count == 0
