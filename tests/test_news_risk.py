"""事件日历与新闻风控单元测试。"""

from datetime import UTC, datetime, timedelta

from src.services.news_risk import NewsRiskEngine
from src.types.domain.event import MacroCalendarEvent, NewsEvent


def test_macro_calendar_lock() -> None:
    engine = NewsRiskEngine()
    scheduled_t = datetime(2026, 3, 10, 20, 30, tzinfo=UTC)

    event = MacroCalendarEvent(
        event_id="cpi_01",
        event_name="US_CPI",
        scheduled_at=scheduled_t,
        lock_window_hours_before=2.0,
        lock_window_hours_after=1.0,
    )
    engine.register_macro_event(event)

    # 1. 公布前 3 小时：未进入锁定
    t_safe = scheduled_t - timedelta(hours=3)
    locked, _ = engine.is_event_locked(t_safe)
    assert not locked

    # 2. 公布前 1 小时：进入锁定
    t_locked = scheduled_t - timedelta(hours=1)
    locked, reason = engine.is_event_locked(t_locked)
    assert locked
    assert reason is not None
    assert "US_CPI" in reason

    # 3. 公布后 30 分钟：仍处于冷却锁定
    t_after_locked = scheduled_t + timedelta(minutes=30)
    locked, _ = engine.is_event_locked(t_after_locked)
    assert locked

    # 4. 公布后 2 小时：锁定解除
    t_after_free = scheduled_t + timedelta(hours=2)
    locked, _ = engine.is_event_locked(t_after_free)
    assert not locked


def test_news_event_ingestion_and_flags() -> None:
    engine = NewsRiskEngine(severity_threshold=0.7, confidence_threshold=0.6)
    base_t = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)

    # 低危新闻：不触发高危标志
    news_low = NewsEvent(
        event_id="n_01",
        title="市场常规波动简讯",
        content="内容",
        source="FEED",
        published_at=base_t,
        severity=0.3,
        confidence=0.8,
    )
    flags_low = engine.ingest_news(news_low)
    assert len(flags_low) == 0

    # 高危高置信新闻：触发风险标志
    news_high = NewsEvent(
        event_id="n_02",
        title="突发重大宏观利率调整",
        content="内容",
        source="FEED",
        published_at=base_t,
        event_type="MONETARY",
        severity=0.85,
        confidence=0.9,
    )
    flags_high = engine.ingest_news(news_high)
    assert len(flags_high) == 1
    assert "NEWS_RISK_MONETARY_n_02" in flags_high

    active = engine.get_active_risk_flags(base_t + timedelta(hours=2))
    assert "NEWS_RISK_MONETARY_n_02" in active
