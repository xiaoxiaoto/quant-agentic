"""多品种参考行情的采集隔离和时间语义验证。"""

import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
from src.clients.jd_gold import JDGoldClient
from src.contracts.domains.reference_quote import ReferenceProductCatalog
from src.contracts.enums.trading import DataQualityState
from src.repositories.reference_quote import ReferenceQuoteRepository
from src.runtime.dependencies import RuntimeDependencies
from src.services.market.live_quotes import LiveQuoteCollector
from tests.test_live_quotes import envelope, runtime_for


def market_response(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/latestPrice"):
        return httpx.Response(200, json=envelope(datetime.now(UTC)))
    if request.url.path.endswith("/queryStockData"):
        code = json.loads(request.content)["paramMap"]["stockCode"]
        return httpx.Response(
            200,
            json={
                "success": True,
                "resultCode": 0,
                "resultData": {
                    "data": {
                        "stockCode": code.removeprefix("SGE-"),
                        "currentPrice": "900",
                        "currentTime": str(int(datetime.now(UTC).timestamp() * 1000)),
                    }
                },
            },
        )
    code = request.url.params["uniqueCode"]
    if code == "CGB-JCJ0":
        return httpx.Response(503)
    return httpx.Response(
        200,
        json={
            "code": 200,
            "data": {
                "uniqueCode": code,
                "lastPrice": "812.16",
                "tradeDateTime": "{}",
            },
        },
    )


def test_collect_all_isolates_failures_and_preserves_missing_time(tmp_path: Path) -> None:
    runtime = runtime_for(tmp_path)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(market_response)) as http:
            runtime.live_quotes.client = JDGoldClient(http)
            statuses = await runtime.live_quotes.collect_all()
            assert len(statuses) == len(ReferenceProductCatalog.all())
            by_code = {status.product_code: status for status in statuses}
            assert by_code["CGB-JCJ0"].failures == 1
            assert by_code["CGB-JCJ0"].latest is None
            assert by_code["CMBC-JCJ"].latest is not None
            quote = by_code["ICBC-JCJ"].latest
            assert quote is not None
            assert quote.quote_at is None
            assert quote.quality_state == DataQualityState.GAP
            assert quote.available_at is None
            assert quote.is_tradable is False
            international = by_code["WG-XAUUSD"].latest
            assert international is not None
            assert international.unit == "USD/oz"
            saved = by_code["ICBC-JCJ"].records_saved
            await runtime.live_quotes.collect_all()
            assert runtime.live_quotes.status("ICBC-JCJ").records_saved == saved

    asyncio.run(run())
    restored = runtime_for(tmp_path)
    restored_quote = restored.live_quotes.latest("ICBC-JCJ")
    assert restored_quote is not None
    assert restored_quote.price == 812.16
    assert restored_quote.quote_at is None
    assert runtime.quotes.get_latest_as_of(datetime.now(UTC)) is None


@pytest.mark.parametrize(("code", "unit"), [("SGE-Au99.99", "CNY/g"), ("SGE-Ag(T+D)", "CNY/kg")])
def test_sge_uses_real_source_time_and_correct_units(tmp_path: Path, code: str, unit: str) -> None:
    runtime = runtime_for(tmp_path)
    now = datetime.now(UTC)

    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/queryStockData")
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "resultCode": 0,
                    "resultData": {
                        "data": {
                            "stockCode": code.removeprefix("SGE-"),
                            "currentPrice": "900",
                            "currentTime": str(int(now.timestamp() * 1000)),
                        }
                    },
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            runtime.live_quotes.client = JDGoldClient(http)
            quote = await runtime.live_quotes.collect_once(code)
            assert quote.quote_at is not None
            assert abs((quote.quote_at - now).total_seconds()) < 0.001
            assert quote.unit == unit
            assert quote.product_code == code

    asyncio.run(run())


def test_multi_product_routes_and_unknown_symbol(tmp_path: Path) -> None:
    runtime = runtime_for(tmp_path)
    http = httpx.AsyncClient(transport=httpx.MockTransport(market_response))
    runtime.live_quotes.client = JDGoldClient(http)
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        with TestClient(app) as client:
            products = client.get("/api/v1/quotes/live/products").json()
            assert len(products) == 28
            assert client.post("/api/v1/quotes/live/collect/all").status_code == 200
            subset_resp = client.post(
                "/api/v1/quotes/live/collect/all", params=[("product_codes", "CMBC-JCJ"), ("product_codes", "CIB-JCJ0")]
            )
            assert subset_resp.status_code == 200
            assert len(subset_resp.json()) == 2
            assert (
                client.post("/api/v1/quotes/live/collect/all", params=[("product_codes", "INVALID-CODE")]).status_code
                == 422
            )
            quote = client.get("/api/v1/quotes/live/latest", params={"product_code": "CIB-JCJ0"}).json()
            assert quote["product_code"] == "CIB-JCJ0"
            assert quote["price_side"] == "buy"
            assert len(client.get("/api/v1/quotes/live/status/all").json()) == 28
            assert client.get("/api/v1/quotes/live/latest/all").status_code == 200
            for path in ("latest", "status"):
                assert client.get(f"/api/v1/quotes/live/{path}", params={"product_code": "unknown"}).status_code == 422
            assert client.post("/api/v1/quotes/live/collect", params={"product_code": "unknown"}).status_code == 422
    finally:
        app.dependency_overrides.clear()
        asyncio.run(http.aclose())


@pytest.mark.parametrize(
    "changes",
    [
        {"uniqueCode": "CZB-JCJ"},
        {"lastPrice": "NaN"},
        {"lastPrice": True},
        {"lastPrice": -1},
        {"tradeDateTime": "bad"},
        {"tradeDateTime": "2026-11-18"},
        {"demode": True},
    ],
)
def test_invalid_market_data_does_not_replace_previous_quote(tmp_path: Path, changes: dict) -> None:
    runtime = runtime_for(tmp_path)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(market_response)) as http:
            runtime.live_quotes.client = JDGoldClient(http)
            first = await runtime.live_quotes.collect_once("ICBC-JCJ")
        data = {"uniqueCode": "ICBC-JCJ", "lastPrice": "900", "tradeDateTime": "{}"} | changes
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"code": 200, "data": data}))
        ) as http:
            runtime.live_quotes.client = JDGoldClient(http)
            with pytest.raises(ValueError):
                await runtime.live_quotes.collect_once("ICBC-JCJ")
        assert runtime.live_quotes.latest("ICBC-JCJ") == first
        assert runtime.live_quotes.status("ICBC-JCJ").failures == 1

    asyncio.run(run())


def test_list_response_matches_requested_product_and_uses_china_timezone(tmp_path: Path) -> None:
    runtime = runtime_for(tmp_path)
    payload = [
        {"uniqueCode": "CZB-JCJ", "lastPrice": "700", "tradeDateTime": "{}"},
        {"uniqueCode": "ICBC-JCJ", "lastPrice": "800", "tradeDateTime": "2026-11-18T12:00:00"},
    ]

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"code": 200, "data": payload}))
        ) as http:
            runtime.live_quotes.client = JDGoldClient(http)
            quote = await runtime.live_quotes.collect_once("ICBC-JCJ")
            assert quote.price == 800
            assert quote.quote_at == datetime(2026, 11, 18, 4, tzinfo=UTC)

    asyncio.run(run())


def test_default_background_collects_all_with_bounded_concurrency(tmp_path: Path) -> None:
    async def run() -> None:
        active = 0
        maximum = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(0.001)
                return market_response(request)
            finally:
                active -= 1

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            collector = LiveQuoteCollector(
                JDGoldClient(http),
                ReferenceQuoteRepository(tmp_path),
                enabled=True,
                interval_seconds=60,
                stale_seconds=30,
            )
            await collector.start()
            try:
                async with asyncio.timeout(2):
                    while not all(collector.status(p.code).last_attempt_at for p in ReferenceProductCatalog.all()):
                        await asyncio.sleep(0.001)
                    while collector.status("SGE-NYAuTN12").records_saved == 0:
                        await asyncio.sleep(0.001)
                assert 1 < maximum <= 3
                assert collector.status("CGB-JCJ0").failures == 1
                assert collector.latest("SGE-NYAuTN12") is not None
            finally:
                await collector.stop()
            assert collector.status().running is False

    asyncio.run(run())


def test_overlapping_batches_and_single_collection_share_concurrency_limit(tmp_path: Path) -> None:
    runtime = runtime_for(tmp_path)

    async def run() -> None:
        active = 0
        maximum = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(0.001)
                return market_response(request)
            finally:
                active -= 1

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            runtime.live_quotes.client = JDGoldClient(http)
            await asyncio.gather(
                runtime.live_quotes.collect_all(),
                runtime.live_quotes.collect_all(),
                runtime.live_quotes.collect_once("ICBC-JCJ"),
            )
        assert maximum == 3

    asyncio.run(run())


def test_cancel_waits_for_persistence_and_memory_commit(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class SlowRepository(ReferenceQuoteRepository):
        def save(self, quote) -> None:
            entered.set()
            if not release.wait(2):
                raise OSError("测试等待写盘超时")
            super().save(quote)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(market_response)) as http:
            collector = LiveQuoteCollector(
                JDGoldClient(http),
                SlowRepository(tmp_path),
                enabled=True,
                interval_seconds=60,
                stale_seconds=30,
            )
            collecting = asyncio.create_task(collector.collect_once("ICBC-JCJ"))
            try:
                async with asyncio.timeout(1):
                    while not entered.is_set():
                        await asyncio.sleep(0.001)
                collecting.cancel()
                await asyncio.sleep(0)
                assert collector._lock.locked()
                assert collecting.done() is False
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await collecting
            assert collector._lock.locked() is False
            assert collector.status("ICBC-JCJ").records_saved == 1
            assert collector.latest("ICBC-JCJ") is not None
            assert collector.latest("ICBC-JCJ") == ReferenceQuoteRepository(tmp_path).latest("ICBC-JCJ")

    asyncio.run(run())


def test_collect_all_deduplicates_and_serializes_same_product(tmp_path: Path) -> None:
    async def run() -> None:
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return market_response(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            collector = LiveQuoteCollector(
                JDGoldClient(http),
                ReferenceQuoteRepository(tmp_path),
                enabled=True,
                interval_seconds=60,
                stale_seconds=30,
            )
            statuses = await collector.collect_all(("ICBC-JCJ", "ICBC-JCJ", "CMBC-JCJ"))
            assert [s.product_code for s in statuses] == ["ICBC-JCJ", "CMBC-JCJ"]
            assert calls == 2

    asyncio.run(run())
