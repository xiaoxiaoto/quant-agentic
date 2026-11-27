"""验证外部 K 线解析、增量写入和来源隔离。"""

import lzma
import struct
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo

import httpx
import pytest

from src.clients.external_bars import ExternalBarClient
from src.contracts.domains.market_bar import KLineBar
from src.repositories.market_bar import MarketBarRepository
from src.runtime.external_bars import ExternalBarsCLI
from src.services.market.external_bars import ExternalBarSync
from src.services.market.xauusd_merge import XauusdBarMerge


def test_external_bars_sync_by_source_and_interval(tmp_path: Path) -> None:
    minute_start = datetime(2025, 1, 2, 15, tzinfo=UTC)
    timestamps = [int((minute_start + timedelta(minutes=i)).timestamp() * 1000) for i in range(60)]
    duka = "\n".join(["timestamp,open,high,low,close", *[f"{t},20,22,19,21" for t in timestamps]])
    hist_zip = BytesIO()
    with ZipFile(hist_zip, "w") as archive:
        archive.writestr("data.csv", "20250102 100000;25;26;24;25.5;0\n")
    sge_close = 810

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "www.sge.com.cn":
            return httpx.Response(
                200, json={"time": [["2025-01-02", 800, sge_close, 790, 820], ["2025-01-03", 800, 810, 799, 805]]}
            )
        if host == "raw.githubusercontent.com":
            return httpx.Response(200, text=duka)
        if host == "www.histdata.com":
            if request.method == "GET":
                return httpx.Response(200, text='<input name="tk" value="' + "a" * 32 + '">')
            return httpx.Response(200, content=hist_zip.getvalue())
        raise AssertionError(f"未知数据源：{host}")

    repository = MarketBarRepository(tmp_path / "quotes.sqlite3")
    sources = ("sge_daily", "dukascopy", "histdata")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        sync = ExternalBarSync(repository, ExternalBarClient(http))
        start, end = date(2025, 1, 2), date(2025, 1, 4)
        initial = sync.run(sources, start=start, end=end)
        repeated = sync.run(sources, start=start, end=end)
        sge_close = 811
        updated = sync.run(("sge_daily",), start=start, end=end)
        incremental = sync.run(("sge_daily",), end=end)
    assert all(report.error is None for report in initial.values())
    assert initial["sge_daily/day"].rejected == 1
    assert initial["dukascopy_bid/m1"].written == {"m1": 60, "m5": 12, "m15": 4, "m30": 2, "m60": 1}
    assert initial["dukascopy_ask/m1"].written["m60"] == 1
    assert all(count == 0 for report in repeated.values() for count in report.written.values())
    assert updated["sge_daily/day"].written["day"] == 1
    assert incremental["sge_daily/day"].written["day"] == 0
    assert len(repository.query_klines("SGE-Au99.99", "day")) == 1
    sge_bar = repository.query_klines("SGE-Au99.99", "day", source="sge_daily")[0]
    assert sge_bar.close_price == 811
    assert sge_bar.available_at == datetime(2025, 1, 2, 7, 30, tzinfo=UTC)
    hist_bar = repository.query_klines("WG-XAUUSD", "m1", source="histdata_bid")[0]
    assert hist_bar.bar_time == minute_start
    assert hist_bar.volume is None


def test_dukascopy_commits_cutoff_archive_before_live_feed_failure(tmp_path: Path) -> None:
    end = datetime(2026, 10, 19, 14, tzinfo=UTC)
    stamp = int((end - timedelta(minutes=1)).timestamp() * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text=f"timestamp,open,high,low,close\n{stamp},20,22,19,21\n")
        return httpx.Response(503)

    repository = MarketBarRepository(tmp_path / "quotes.sqlite3")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        sync = ExternalBarSync(repository, ExternalBarClient(http))
        reports = sync.run(("dukascopy",), start=end.date(), end=end.date() + timedelta(days=1))
    for source in ("dukascopy_bid", "dukascopy_ask"):
        assert reports[f"{source}/m1"].error
        assert reports[f"{source}/m1"].written["m1"] == 1
        assert repository.query_klines("WG-XAUUSD", "m1", source=source)[0].available_at == end


def test_dukascopy_binary_unpack_session_filter_and_non_utc_range(tmp_path: Path) -> None:
    day = date(2026, 10, 20)
    # 2026-10-20 为美东夏令时（UTC-4）：21:00 UTC 对应纽约 17:00 维护时段，22:00 UTC 对应纽约 18:00 开盘。
    payload = lzma.compress(
        struct.pack(">IIIIIf", 21 * 3600, 3000100, 3000500, 2999900, 3000800, 12.5)
        + struct.pack(">IIIIIf", 22 * 3600, 3001000, 3001500, 3000900, 3001800, 25.0)
    )
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=payload)

    shanghai = ZoneInfo("Asia/Shanghai")
    start_cst = datetime(2026, 10, 20, 8, tzinfo=shanghai)
    end_cst = datetime(2026, 10, 21, 8, tzinfo=shanghai)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        candles = list(ExternalBarClient(http).dukascopy("bid", start_cst, end_cst))
    assert requested_urls == ["https://datafeed.dukascopy.com/datafeed/XAUUSD/2026/09/20/BID_candles_min_1.bi5"]
    assert len(candles) == 2
    assert candles[1].time == datetime(2026, 10, 20, 22, tzinfo=UTC)
    assert (candles[1].open, candles[1].high, candles[1].low, candles[1].close) == (3001.0, 3001.8, 3000.9, 3001.5)
    assert candles[1].volume == pytest.approx(25.0)
    assert XauusdBarMerge.session_date(datetime(2026, 10, 23, 21, tzinfo=UTC)) is None
    assert XauusdBarMerge.session_date(datetime(2026, 10, 25, 21, tzinfo=UTC)) is None
    assert XauusdBarMerge.session_date(datetime(2026, 10, 25, 22, tzinfo=UTC)) == date(2026, 10, 26)

    repository = MarketBarRepository(tmp_path / "session_filter.sqlite3")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        reports = ExternalBarSync(repository, ExternalBarClient(http)).run(
            ("dukascopy",), start=day, end=day + timedelta(days=1)
        )
    assert reports["dukascopy_bid/m1"].written["m1"] == 1
    saved = repository.query_klines("WG-XAUUSD", "m1", source="dukascopy_bid")
    assert [bar.bar_time for bar in saved] == [datetime(2026, 10, 20, 22, tzinfo=UTC)]


def test_histdata_non_zip_fallback_and_csv_empty_lines() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.histdata.com":
            if request.method == "GET":
                return httpx.Response(200, text='<input name="tk" value="' + "b" * 32 + '">')
            return httpx.Response(200, text="<html>rate limited</html>")
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text="\n20250102 100000;25;26;24;25.5;0\n\n")
        raise AssertionError(f"未知请求：{request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = ExternalBarClient(http)
        rows = list(client.histdata(2025, None))
        assert len(rows) == 1
        assert rows[0].close == 25.5
        with pytest.raises(BadZipFile):
            list(client.histdata(2026, 5))


def test_repository_updates_when_pit_metadata_changes(tmp_path: Path) -> None:
    repository = MarketBarRepository(tmp_path / "quotes.sqlite3")
    stamp = datetime(2025, 1, 2, 15, tzinfo=UTC)
    base = KLineBar(
        product_code="WG-XAUUSD",
        interval="m60",
        source="dukascopy_bid",
        bar_time=stamp,
        trade_date="2025-01-02",
        open_price=2600.0,
        high_price=2610.0,
        low_price=2595.0,
        close_price=2605.0,
        collected_at=stamp + timedelta(hours=1),
        available_at=None,
    )
    assert repository.save_klines_with_counts({"m60": [base]}) == {"m60": 1}
    assert repository.save_klines_with_counts({"m60": [base]}) == {"m60": 0}
    updated = base.model_copy(update={"available_at": stamp + timedelta(hours=1)})
    assert repository.save_klines_with_counts({"m60": [updated]}) == {"m60": 1}
    saved = repository.query_klines("WG-XAUUSD", "m60", source="dukascopy_bid")[0]
    assert saved.available_at == stamp + timedelta(hours=1)


def test_external_bars_cli_rejects_invalid_date_range() -> None:
    with pytest.raises(SystemExit) as exc_info:
        ExternalBarsCLI.run(["--start", "2025-01-05", "--end", "2025-01-05"])
    assert exc_info.value.code == 2


def test_dukascopy_validates_side_empty_range_and_fetches_multiple_months() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        stamp = int(datetime(2025, 1, 2, 15, tzinfo=UTC).timestamp() * 1000)
        return httpx.Response(200, text=f"timestamp,open,high,low,close\n{stamp},20,22,19,21\n")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = ExternalBarClient(http)
        with pytest.raises(ValueError, match="bid 或 ask"):
            list(client.dukascopy("mid", datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 2, tzinfo=UTC)))  # type: ignore[arg-type]
        assert list(client.dukascopy("bid", datetime(2025, 1, 2, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC))) == []
        assert requested == []

        rows = list(client.dukascopy("bid", datetime(2025, 1, 15, tzinfo=UTC), datetime(2025, 3, 1, tzinfo=UTC)))
        assert len(rows) == 2
        assert len(requested) == 2
        assert requested[0].endswith("xauusd_bid_m1_2025_01.csv")
        assert requested[1].endswith("xauusd_bid_m1_2025_02.csv")


def test_histdata_validates_month_and_does_not_replay_backup_after_partial_yield() -> None:
    bad_zip = BytesIO()
    with ZipFile(bad_zip, "w") as archive:
        archive.writestr("data.csv", "20250102 100000;25;26;24;25.5;0\nbroken_row\n")
    backup_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal backup_called
        if request.url.host == "www.histdata.com":
            if request.method == "GET":
                return httpx.Response(200, text='<input name="tk" value="' + "c" * 32 + '">')
            return httpx.Response(200, content=bad_zip.getvalue())
        backup_called = True
        return httpx.Response(200, text="20250102 100000;25;26;24;25.5;0\n")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = ExternalBarClient(http)
        with pytest.raises(ValueError, match="月份"):
            list(client.histdata(2025, 13))
        with pytest.raises(ValueError, match="行列数不足"):
            list(client.histdata(2025, None))
        with pytest.raises(ValueError, match="时区"):
            list(client.dukascopy("bid", datetime(2025, 1, 1), datetime(2025, 1, 2)))
    assert backup_called is False


def test_histdata_explicit_start_uses_histdata_timezone(tmp_path: Path) -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(404)

    repo = MarketBarRepository(tmp_path / "market.sqlite3")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        sync = ExternalBarSync(repo, ExternalBarClient(http))
        sync.run(["histdata"], start=date(2024, 1, 1), end=date(2024, 1, 2))
    assert requested_urls
    assert all("/2023" not in url for url in requested_urls)
