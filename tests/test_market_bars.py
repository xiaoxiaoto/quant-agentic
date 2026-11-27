"""多周期 K 线与分时行情采集、仓储与路由测试。"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.clients.jd_gold import JDGoldError
from src.configs.settings import Settings
from src.contracts.domains.jd_auth import JDGoldAuthState
from src.contracts.domains.market_bar import KLineBar, TimeSharingPoint
from src.repositories.market_bar import MarketBarRepository
from src.routers.v1 import quotes as quotes_router
from src.runtime.dependencies import RuntimeDependencies
from src.services.market.bar_collector import MarketBarCollector


def _make_gateway(*, authorized: bool = True) -> MagicMock:
    gateway = MagicMock()
    token = SecretStr("test-token") if authorized else SecretStr("")
    auth_state = JDGoldAuthState(access_token=token)
    gateway.auth = MagicMock()
    gateway.auth.auth_state_async = AsyncMock(return_value=auth_state)

    gateway.market = MagicMock()
    gateway.market.get_stock_kline = AsyncMock(
        return_value={
            "kLineDtoList": [
                {
                    "date": "2026-11-24",
                    "open": "760.00",
                    "high": "770.00",
                    "low": "755.00",
                    "close": "767.00",
                    "volume": "1000",
                },
                {
                    "date": "2026-11-25",
                    "open": "767.00",
                    "high": "784.00",
                    "low": "754.00",
                    "close": "758.50",
                    "volume": "2000",
                },
                # 含非法布尔价格的行应被安全忽略
                {"date": "2026-11-26", "open": True, "high": "780", "low": "750", "close": "760"},
            ]
        }
    )

    gateway.trade = MagicMock()
    gateway.trade.get_sim_kline = AsyncMock(
        return_value={
            "items": [
                {
                    "tradeDate": "2026-11-25",
                    "tradeTime": "2026-11-25 10:00:00",
                    "openPrice": 765.1,
                    "highPrice": 766.2,
                    "lowPrice": 764.8,
                    "closePrice": 765.9,
                    "preClose": 765.0,
                    "volume": 10,
                    "turnover": 7659.0,
                },
                {
                    "tradeDate": "2026-11-25",
                    "tradeTime": "2026-11-25 10:05:00",
                    "openPrice": 765.9,
                    "highPrice": 767.0,
                    "lowPrice": 765.5,
                    "closePrice": 766.5,
                    "preClose": 765.9,
                    "volume": 12,
                    "turnover": 9198.0,
                },
            ]
        }
    )
    gateway.trade.get_sim_time_sharing = AsyncMock(
        return_value={
            "items": [
                {
                    "tradeTime": "2026-11-25 09:31:00",
                    "showTime": "09:31",
                    "lastPrice": 766.12,
                    "preClose": 767.10,
                    "changePercent": -0.0012,
                    "tradeVolume": 0,
                    "totalVolume": 0,
                    "turnover": 0,
                },
                {
                    "tradeTime": "2026-11-25 09:32:00",
                    "showTime": "09:32",
                    "lastPrice": 766.35,
                    "preClose": 767.10,
                    "changePercent": -0.0009,
                    "tradeVolume": 0,
                    "totalVolume": 0,
                    "turnover": 0,
                },
            ]
        }
    )
    return gateway


def test_market_bar_repository_upsert_and_query(tmp_path: Path) -> None:
    """仓储应按唯一键幂等更新 K 线与分时数据并支持区间查询。"""
    repo = MarketBarRepository(tmp_path / "bars.sqlite3")
    now = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
    bar1 = KLineBar(
        product_code="SGE-Au99.99",
        interval="day",
        source="jd_public_kline",
        bar_time=now - timedelta(days=1),
        trade_date="2026-11-24",
        open_price=760.0,
        high_price=770.0,
        low_price=755.0,
        close_price=765.0,
        collected_at=now,
    )
    bar2 = KLineBar(
        product_code="SGE-Au99.99",
        interval="day",
        source="jd_public_kline",
        bar_time=now,
        trade_date="2026-11-25",
        open_price=765.0,
        high_price=780.0,
        low_price=754.0,
        close_price=758.0,
        collected_at=now,
    )
    assert repo.save_klines([bar1, bar2]) == 2
    # 未变化的重复柱应跳过写入，不覆盖原 collected_at
    later = now + timedelta(minutes=5)
    assert repo.save_klines([bar1.model_copy(update={"collected_at": later})]) == 0
    assert repo.query_klines("SGE-Au99.99", "day", source="jd_public_kline")[0].collected_at == now
    # 同一时间柱更新收盘价应仅写入该 1 条变动记录
    bar2_updated = bar2.model_copy(update={"close_price": 759.5, "collected_at": later})
    assert repo.save_klines([bar1, bar2_updated]) == 1

    rows = repo.query_klines("SGE-Au99.99", "day", source="jd_public_kline")
    assert len(rows) == 2
    assert rows[0].trade_date == "2026-11-24"
    assert rows[1].close_price == 759.5

    pt1 = TimeSharingPoint(
        product_code="WG-JDAU",
        granularity="m1",
        trade_time=now - timedelta(minutes=1),
        show_time="17:59",
        last_price=758.1,
        collected_at=now,
    )
    pt2 = TimeSharingPoint(
        product_code="WG-JDAU",
        granularity="m1",
        trade_time=now,
        show_time="18:00",
        last_price=758.5,
        collected_at=now,
    )
    assert repo.save_time_sharing([pt1, pt2]) == 2
    assert repo.save_time_sharing([pt1.model_copy(update={"collected_at": later})]) == 0
    assert repo.save_time_sharing([pt1, pt2.model_copy(update={"last_price": 758.9})]) == 1
    ts_rows = repo.query_time_sharing("WG-JDAU", "m1")
    assert len(ts_rows) == 2
    assert ts_rows[-1].last_price == 758.9


def test_market_bar_collector_validates_configuration(tmp_path: Path) -> None:
    """非法品种、重复周期或未知分时颗粒度必须在初始化时拒绝。"""
    repo = MarketBarRepository(tmp_path / "bars.sqlite3")
    gateway = _make_gateway()

    with pytest.raises(ValueError, match="不支持的参考行情品种"):
        MarketBarCollector(
            gateway,
            repo,
            enabled=True,
            interval_seconds=60,
            kline_products=("UNKNOWN-CODE",),
            kline_intervals=("day",),
            timesharing_intervals=("m1",),
        )
    with pytest.raises(ValueError, match="不支持的 K 线周期"):
        MarketBarCollector(
            gateway,
            repo,
            enabled=True,
            interval_seconds=60,
            kline_products=("CMBC-JCJ",),
            kline_intervals=("m2",),
            timesharing_intervals=("m1",),
        )
    with pytest.raises(ValueError, match="不支持的分时颗粒度"):
        MarketBarCollector(
            gateway,
            repo,
            enabled=True,
            interval_seconds=60,
            kline_products=("CMBC-JCJ",),
            kline_intervals=("day",),
            timesharing_intervals=("m15",),
        )


def test_market_bar_collector_collects_public_and_sim_when_authorized(tmp_path: Path) -> None:
    """授权有效时应同时采集公开日/周/月 K 线、多周期模拟 K 线与分时数据。"""
    repo = MarketBarRepository(tmp_path / "bars.sqlite3")
    gateway = _make_gateway(authorized=True)
    collector = MarketBarCollector(
        gateway,
        repo,
        enabled=True,
        interval_seconds=60,
        kline_products=("CMBC-JCJ", "SGE-Au99.99"),
        kline_intervals=("m1", "m5", "day", "week"),
        timesharing_intervals=("m1",),
    )

    status = asyncio.run(collector.collect_once())
    assert status.auth_available is True
    assert status.failures == 0
    assert status.last_error is None
    assert status.last_success_at is not None
    # 公开 K 线：2 品种 × 2 周期（day/week） × 2 有效柱 = 8；模拟 K 线：4 周期 × 2 柱 = 8
    assert status.kline_bars_saved == 16
    assert status.timesharing_points_saved == 2

    assert len(collector.query_klines("SGE-Au99.99", "day", source="jd_public_kline")) == 2
    assert len(collector.query_klines("WG-JDAU", "m5", source="jd_sim_kline")) == 2
    assert len(collector.query_time_sharing("WG-JDAU", "m1")) == 2

    # 非强制轮询时：慢变周期（day/week）在冷却期内跳过上游请求，且数据未变化时新增写入为 0
    gateway.market.get_stock_kline.reset_mock()
    gateway.trade.get_sim_kline.reset_mock()
    gateway.trade.get_sim_time_sharing.reset_mock()
    second = asyncio.run(collector.collect_once(force=False))
    assert second.kline_bars_saved == 16
    assert second.timesharing_points_saved == 2
    gateway.market.get_stock_kline.assert_not_called()
    assert [call.kwargs["k_type"] for call in gateway.trade.get_sim_kline.await_args_list] == ["m1", "m5"]
    gateway.trade.get_sim_time_sharing.assert_awaited_once()


def test_market_bar_collector_skips_sim_without_auth_and_isolates_errors(tmp_path: Path) -> None:
    """未授权时仅采集公开 K 线，且单品种失败不阻断其余品种。"""
    repo = MarketBarRepository(tmp_path / "bars.sqlite3")
    gateway = _make_gateway(authorized=False)

    async def flaky_kline(code: str, k_type: str = "day") -> dict[str, object]:
        if code == "CMBC-JCJ":
            raise JDGoldError("上游超时")
        return {
            "kLineDtoList": [
                {"date": "2026-11-25", "open": "760", "high": "770", "low": "755", "close": "765"},
            ]
        }

    gateway.market.get_stock_kline = AsyncMock(side_effect=flaky_kline)
    collector = MarketBarCollector(
        gateway,
        repo,
        enabled=True,
        interval_seconds=60,
        kline_products=("CMBC-JCJ", "SGE-Au99.99"),
        kline_intervals=("m5", "day"),
        timesharing_intervals=("m1",),
    )

    status = asyncio.run(collector.collect_once())
    assert status.auth_available is False
    assert status.failures == 1
    assert status.kline_bars_saved == 1
    assert status.timesharing_points_saved == 0
    assert status.last_error is not None
    assert "CMBC-JCJ/day" in status.last_error
    gateway.trade.get_sim_kline.assert_not_called()
    gateway.trade.get_sim_time_sharing.assert_not_called()


def test_market_bar_router_endpoints(tmp_path: Path) -> None:
    """行情路由应正确暴露 K 线、分时查询与状态接口并校验边界参数。"""
    settings = Settings(
        data_dir=str(tmp_path),
        live_quotes_enabled=False,
        market_bars_enabled=False,
        news_sync_enabled=False,
        jd_holdings_sync_enabled=False,
        jd_proactive_auth_enabled=False,
        timesfm_enabled=False,
        news_enabled=False,
    )
    runtime = RuntimeDependencies(settings)
    app = FastAPI()
    app.include_router(quotes_router.router, prefix="/api/v1")
    app.state.runtime = runtime

    client = TestClient(app)
    status_resp = client.get("/api/v1/quotes/bars/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["enabled"] is False

    collect_resp = client.post("/api/v1/quotes/bars/collect")
    assert collect_resp.status_code == 503

    bad_range = client.get(
        "/api/v1/quotes/bars/kline",
        params={
            "product_code": "SGE-Au99.99",
            "interval": "day",
            "start_time": "2026-11-26T00:00:00Z",
            "end_time": "2026-11-25T00:00:00Z",
        },
    )
    assert bad_range.status_code == 400

    bad_interval = client.get(
        "/api/v1/quotes/bars/kline",
        params={"product_code": "SGE-Au99.99", "interval": "m2"},
    )
    assert bad_interval.status_code == 422

    stamp = datetime(2025, 7, 7, 13, tzinfo=UTC)
    MarketBarRepository(settings.database_path).save_klines(
        [
            KLineBar(
                product_code="WG-XAUUSD",
                interval="m5",
                source="dukascopy_bid",
                bar_time=stamp,
                trade_date="2025-07-07",
                open_price=3300,
                high_price=3302,
                low_price=3298,
                close_price=3301,
                collected_at=stamp + timedelta(minutes=5),
            ),
        ]
    )
    params = {"product_code": "WG-XAUUSD", "interval": "m5"}
    merged = client.get("/api/v1/quotes/bars/kline", params=params)
    assert merged.status_code == 200
    assert [bar["source"] for bar in merged.json()] == ["xauusd_merged"]
    assert [bar["origin_source"] for bar in merged.json()] == ["dukascopy_bid"]
    raw = client.get("/api/v1/quotes/bars/kline", params={**params, "source": "dukascopy_bid"})
    assert raw.status_code == 200
    assert [bar["source"] for bar in raw.json()] == ["dukascopy_bid"]
    week = client.get("/api/v1/quotes/bars/kline", params={**params, "interval": "week"})
    assert week.status_code == 422
    non_xauusd_merged = client.get(
        "/api/v1/quotes/bars/kline",
        params={"product_code": "CMBC-JCJ", "interval": "day", "source": "xauusd_merged"},
    )
    assert non_xauusd_merged.status_code == 422

    ok_ts = client.get("/api/v1/quotes/bars/timesharing", params={"product_code": "WG-JDAU", "granularity": "m1"})
    assert ok_ts.status_code == 200
    assert ok_ts.json() == []


def test_live_quote_history_filters_in_sql_before_limit(tmp_path: Path) -> None:
    """休市或异常期连续写入大量 STALE/GAP 记录时，/live/history 仍应返回有效的 NORMAL 历史点位。"""
    from src.contracts.domains.reference_quote import ReferenceQuote
    from src.contracts.enums.trading import DataQualityState
    from src.repositories.reference_quote import ReferenceQuoteRepository

    settings = Settings(
        data_dir=str(tmp_path),
        live_quotes_enabled=False,
        market_bars_enabled=False,
        news_sync_enabled=False,
        jd_holdings_sync_enabled=False,
        jd_proactive_auth_enabled=False,
        timesfm_enabled=False,
        news_enabled=False,
    )
    runtime = RuntimeDependencies(settings)
    repo = ReferenceQuoteRepository(tmp_path / "live_quotes", database_path=settings.database_path)
    base = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    for idx in range(2):
        ts = base + timedelta(minutes=idx)
        repo.save(
            ReferenceQuote(
                quote_id=f"normal-{idx}",
                source_id="JD_API",
                product_code="CMBC-JCJ",
                unit="CNY/g",
                price_side="reference",
                price=760.0 + idx,
                quote_at=ts,
                received_at=ts,
                available_at=ts,
                is_tradable=False,
                quality_state=DataQualityState.NORMAL,
                payload_hash=f"h-normal-{idx}",
            )
        )
    for idx in range(5):
        ts = base + timedelta(minutes=10 + idx)
        repo.save(
            ReferenceQuote(
                quote_id=f"stale-{idx}",
                source_id="JD_API",
                product_code="CMBC-JCJ",
                unit="CNY/g",
                price_side="reference",
                price=761.0,
                quote_at=None if idx == 0 else ts,
                received_at=ts,
                available_at=ts,
                is_tradable=False,
                quality_state=DataQualityState.STALE,
                payload_hash=f"h-stale-{idx}",
            )
        )

    app = FastAPI()
    app.include_router(quotes_router.router, prefix="/api/v1")
    app.state.runtime = runtime
    client = TestClient(app)

    resp = client.get("/api/v1/quotes/live/history", params={"product_code": "CMBC-JCJ", "limit": 2})
    assert resp.status_code == 200
    assert [price for _ts, price in resp.json()] == [760.0, 761.0]
