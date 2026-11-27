"""验证 XAUUSD 合并行情的取源、交易日和可用时间。"""

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from src.contracts.domains.market_bar import KLineBar, KLineSource
from src.contracts.exceptions import QlibDatasetError
from src.contracts.schemas.qlib import QlibExportRequest
from src.repositories.market_bar import MarketBarRepository
from src.repositories.quote import QuoteRepository
from src.services.market.bar_collector import MarketBarCollector
from src.services.market.session_calendar import SessionCalendar
from src.services.market.xauusd_merge import XauusdBarMerge
from src.services.qlib.data_exporter import QlibDataExporter


def _hour(source: KLineSource, stamp: datetime, price: float) -> KLineBar:
    return KLineBar(
        product_code="WG-XAUUSD",
        interval="m60",
        source=source,
        bar_time=stamp,
        trade_date=stamp.date().isoformat(),
        open_price=price,
        high_price=price + 2,
        low_price=price - 1,
        close_price=price + 1,
        collected_at=stamp + timedelta(hours=2),
        available_at=stamp + timedelta(hours=1),
    )


def _jd_day(day: date, *, completed: bool = True) -> KLineBar:
    midnight = datetime.combine(day, time.min, ZoneInfo("Asia/Shanghai"))
    close = datetime.combine(day, time(17), ZoneInfo("America/New_York"))
    return _hour("jd_public_kline", midnight, 3000).model_copy(
        update={
            "interval": "day",
            "available_at": None,
            "collected_at": close + timedelta(hours=1 if completed else -1),
        }
    )


def test_intraday_prioritizes_whole_bid_bar_and_preserves_range(tmp_path: Path) -> None:
    repo = MarketBarRepository(tmp_path / "market.sqlite3")
    stamp = datetime(2025, 7, 7, 13, tzinfo=UTC)
    following = stamp + timedelta(hours=1)
    repo.save_klines(
        [
            _hour("dukascopy_ask", stamp, 4000),
            _hour("dukascopy_bid", stamp, 3000),
            _hour("histdata_bid", stamp, 2000),
            _hour("histdata_bid", following, 2002),
        ]
    )
    merger = XauusdBarMerge(repo)
    bars = merger.query("m60", start_time=stamp, end_time=following, limit=2)
    assert [bar.source for bar in bars] == ["xauusd_merged", "xauusd_merged"]
    assert [bar.origin_source for bar in bars] == ["dukascopy_bid", "histdata_bid"]
    assert [(bar.open_price, bar.high_price, bar.low_price, bar.close_price) for bar in bars] == [
        (3000, 3002, 2999, 3001),
        (2002, 2004, 2001, 2003),
    ]
    assert merger.query("m60", limit=1) == bars[-1:]


def test_daily_uses_one_complete_new_york_session_then_closed_jd(tmp_path: Path) -> None:
    repo = MarketBarRepository(tmp_path / "market.sqlite3")
    winter = date(2025, 1, 6)
    summer = date(2025, 7, 7)
    jd_fallback = date(2025, 7, 8)
    provisional = date(2025, 7, 9)
    repo.save_klines([_jd_day(summer), _jd_day(jd_fallback), _jd_day(provisional, completed=False)])
    for day, utc_close_hour, primary in ((winter, 22, True), (summer, 21, False)):
        close = datetime.combine(day, time(utc_close_hour), UTC)
        hours = [close - timedelta(hours=i) for i in range(23, 0, -1)]
        repo.save_klines(
            [_hour("dukascopy_bid", stamp, 2000 + idx) for idx, stamp in enumerate(hours if primary else hours[1:])]
            + [_hour("histdata_bid", stamp, 2500 + idx) for idx, stamp in enumerate(hours)]
        )

    merger = XauusdBarMerge(repo)
    bars = merger.query("day")
    assert [bar.trade_date for bar in bars] == [winter.isoformat(), summer.isoformat(), jd_fallback.isoformat()]
    assert [bar.origin_source for bar in bars] == ["dukascopy_bid", "histdata_bid", "jd_public_kline"]
    assert [bar.available_at for bar in bars] == [
        datetime.combine(winter, time(22), UTC),
        datetime.combine(summer, time(21), UTC),
        datetime.combine(jd_fallback, time(21), UTC),
    ]
    assert bars[1].bar_time == datetime.combine(summer, time.min, UTC)
    assert (bars[1].open_price, bars[1].high_price, bars[1].low_price, bars[1].close_price) == (2500, 2524, 2499, 2523)
    assert merger.query("day", start_time=bars[1].bar_time, end_time=bars[1].bar_time) == bars[1:2]


def test_export_minute_bars_keeps_distinct_timestamps_and_source(tmp_path: Path) -> None:
    repo = MarketBarRepository(tmp_path / "market.sqlite3")
    stamp = datetime(2025, 7, 7, 13, tzinfo=UTC)
    next_stamp = stamp + timedelta(minutes=5)
    inputs: tuple[tuple[KLineSource, datetime, float], ...] = (
        ("dukascopy_bid", stamp, 3000),
        ("histdata_bid", next_stamp, 3002),
    )
    repo.save_klines(
        [
            _hour(source, start, price).model_copy(
                update={"interval": "m5", "available_at": start + timedelta(minutes=5)}
            )
            for source, start, price in inputs
        ]
    )
    collector = MarketBarCollector(
        MagicMock(),
        repo,
        enabled=False,
        interval_seconds=60,
        kline_products=("WG-XAUUSD",),
        kline_intervals=("m5",),
        timesharing_intervals=("m1",),
    )
    exporter = QlibDataExporter(
        QuoteRepository(tmp_path / "quotes", database_path=tmp_path / "market.sqlite3"),
        SessionCalendar(),
        market_bars=collector,
    )
    out_dir = tmp_path / "qlib_minutes"
    request = QlibExportRequest(source_mode="kline", freq="5min")
    report = exporter.export_local_dataset(request, target_dir=out_dir)
    assert report.manifest.instruments == ["WG-XAUUSD"]
    assert report.manifest.calendar_count == 2
    assert (out_dir / "calendars" / "5min.txt").read_text().splitlines() == [
        "2025-07-07 13:00:00",
        "2025-07-07 13:05:00",
    ]
    features = out_dir / "features" / "wg-xauusd"
    assert np.fromfile(features / "origin_source.5min.bin", dtype="<f4").tolist() == [0.0, 1.0, 2.0]
    assert np.fromfile(features / "close.5min.bin", dtype="<f4").tolist() == [0.0, 3001.0, 3003.0]
    with pytest.raises(QlibDatasetError, match="分钟频须使用 XAUUSD 合并 K 线"):
        exporter.export_local_dataset(QlibExportRequest(freq="1min"), target_dir=out_dir)


def test_intraday_limit_paginates_past_filtered_maintenance_bars(tmp_path: Path) -> None:
    repo = MarketBarRepository(tmp_path / "market.sqlite3")
    # 2025-07-07 为美东夏令时（UTC-4），21:00 UTC 为纽约 17:00 维护时段，应被跳过且不影响 limit=2 的返回条数。
    valid_1 = datetime(2025, 7, 7, 19, tzinfo=UTC)
    valid_2 = datetime(2025, 7, 7, 20, tzinfo=UTC)
    maintenance = datetime(2025, 7, 7, 21, tzinfo=UTC)
    repo.save_klines(
        [
            _hour("histdata_bid", valid_1, 3000),
            _hour("histdata_bid", valid_2, 3005),
            _hour("histdata_bid", maintenance, 3010),
        ]
    )
    bars = XauusdBarMerge(repo).query("m60", limit=2)
    assert [bar.bar_time for bar in bars] == [valid_1, valid_2]


def test_qlib_exporter_daily_aligns_jd_and_external_trade_dates(tmp_path: Path) -> None:
    repo = MarketBarRepository(tmp_path / "market.sqlite3")
    day = date(2025, 7, 8)
    jd_bar = KLineBar(
        product_code="CMBC-JCJ",
        interval="day",
        source="jd_public_kline",
        bar_time=datetime.combine(day, time.min, ZoneInfo("Asia/Shanghai")),
        trade_date=day.isoformat(),
        open_price=770.0,
        high_price=775.0,
        low_price=768.0,
        close_price=772.0,
        collected_at=datetime(2025, 7, 9, 0, tzinfo=UTC),
        available_at=None,
    )
    sge_bar = KLineBar(
        product_code="SGE-Au99.99",
        interval="day",
        source="sge_daily",
        bar_time=datetime.combine(day, time.min, ZoneInfo("Asia/Shanghai")),
        trade_date=day.isoformat(),
        open_price=771.0,
        high_price=776.0,
        low_price=769.0,
        close_price=773.0,
        collected_at=datetime(2025, 7, 9, 0, tzinfo=UTC),
        available_at=datetime(2025, 7, 8, 7, 30, tzinfo=UTC),
    )
    repo.save_klines([jd_bar, sge_bar])
    collector = MarketBarCollector(
        MagicMock(),
        repo,
        enabled=False,
        interval_seconds=60,
        kline_products=("CMBC-JCJ", "SGE-Au99.99"),
        kline_intervals=("day",),
        timesharing_intervals=("m1",),
    )
    exporter = QlibDataExporter(
        QuoteRepository(tmp_path / "quotes", database_path=tmp_path / "market.sqlite3"),
        SessionCalendar(),
        market_bars=collector,
    )
    out_dir = tmp_path / "qlib_daily"
    request = QlibExportRequest(
        source_mode="kline",
        freq="day",
        kline_interval="day",
        instruments=["CMBC-JCJ", "SGE-Au99.99"],
    )
    report = exporter.export_local_dataset(request, target_dir=out_dir)
    assert report.manifest.calendar_count == 1
    assert (out_dir / "calendars" / "day.txt").read_text().splitlines() == ["2025-07-08"]


def test_xauusd_merge_limit_bounds_and_multi_window_intraday_export(tmp_path: Path) -> None:
    repo = MarketBarRepository(tmp_path / "market.sqlite3")
    first = datetime(2025, 1, 6, 14, tzinfo=UTC)
    second = datetime(2025, 3, 10, 14, tzinfo=UTC)
    repo.save_klines(
        [
            _hour("dukascopy_bid", first, 2650),
            _hour("histdata_bid", first, 2600),
            _hour("histdata_bid", second, 2900),
        ]
    )
    merger = XauusdBarMerge(repo)
    assert merger.query("m60", limit=0) == []
    with pytest.raises(ValueError, match="负数"):
        merger.query("m60", limit=-1)

    full = merger.query("m60", limit=None)
    assert [bar.bar_time for bar in full] == [first, second]
    assert [bar.origin_source for bar in full] == ["dukascopy_bid", "histdata_bid"]


def test_daily_window_boundary_prefers_complete_dukascopy_over_jd(tmp_path: Path) -> None:
    repo = MarketBarRepository(tmp_path / "market.sqlite3")
    # 构造超过单窗（60天）的左边界交易日，验证 jd_public_kline 不会抢占可完整合成的 dukascopy_bid。
    recent = date(2025, 7, 7)
    older = date(2025, 5, 7)
    repo.save_klines([_jd_day(older), _jd_day(recent)])
    close = datetime.combine(older, time(21), UTC)
    hours = [close - timedelta(hours=i) for i in range(23, 0, -1)]
    repo.save_klines([_hour("dukascopy_bid", stamp, 3100 + idx) for idx, stamp in enumerate(hours)])

    bars = XauusdBarMerge(repo).query("day", end_time=datetime(2025, 7, 8, tzinfo=UTC), limit=2)
    assert [bar.trade_date for bar in bars] == [older.isoformat(), recent.isoformat()]
    assert [bar.origin_source for bar in bars] == ["dukascopy_bid", "jd_public_kline"]


def test_xauusd_merge_normalizes_naive_datetimes_and_skips_bad_trade_date(tmp_path: Path) -> None:
    repo = MarketBarRepository(tmp_path / "market.sqlite3")
    valid_day = date(2025, 7, 7)
    bad_bar = _jd_day(date(2025, 7, 8)).model_copy(update={"trade_date": "not-a-date"})
    repo.save_klines([_jd_day(valid_day), bad_bar])
    merger = XauusdBarMerge(repo)

    # 传入 naive datetime 不应抛 TypeError，且应跳过异常 trade_date
    bars = merger.query("day", start_time=datetime(2025, 7, 1), end_time=datetime(2025, 7, 10), limit=None)
    assert [bar.trade_date for bar in bars] == [valid_day.isoformat()]
    assert merger.query("day", start_time=datetime(2025, 7, 10), end_time=datetime(2025, 7, 1)) == []
