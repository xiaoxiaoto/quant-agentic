"""实时参考行情采集、隔离与故障恢复测试。"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
from src.clients.jd_gold import JDGoldClient, JDGoldError
from src.configs.settings import Settings
from src.contracts.enums.trading import DataQualityState
from src.runtime.dependencies import RuntimeDependencies


def envelope(now: datetime, **changes: object) -> dict:
    data = {
        "id": 55900453,
        "productSku": "21001001000001",
        "demode": False,
        "price": "812.16",
        "time": str(int(now.timestamp() * 1000)),
    }
    data.update(changes)
    return {"success": True, "resultCode": 0, "resultData": {"status": "SUCCESS", "datas": data}}


def runtime_for(tmp_path: Path, enabled: bool = True) -> RuntimeDependencies:
    return RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            timesfm_enabled=False,
            news_enabled=False,
            live_quotes_enabled=enabled,
            live_quotes_products=("CMBC-JCJ",),
        )
    )


def test_fetch_preserves_source_timestamp_and_reference_semantics(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    runtime = runtime_for(tmp_path)

    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path.endswith("/latestPrice")
            return httpx.Response(200, json=envelope(now))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            runtime.live_quotes.client = JDGoldClient(http)
            quote = await runtime.live_quotes.collect_once()
        assert quote.price == 812.16
        assert quote.quote_at is not None
        assert abs((quote.quote_at - now).total_seconds()) < 0.001
        assert quote.received_at >= now
        assert quote.product_code == "CMBC-JCJ"
        assert quote.unit == "CNY/g"
        assert quote.is_tradable is False
        assert len(quote.payload_hash) == 64
        assert "bid_price" not in quote.model_dump()

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [{"price": "NaN"}, {"price": 0}, {"time": "{}"}, {"time": None}, {"demode": True}, {"productSku": "other"}],
)
def test_invalid_upstream_payload_is_rejected(changes: dict, tmp_path: Path) -> None:
    runtime = runtime_for(tmp_path)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=envelope(datetime.now(UTC), **changes)))
        ) as http:
            runtime.live_quotes.client = JDGoldClient(http)
            with pytest.raises(ValueError):
                await runtime.live_quotes.collect_once()

    asyncio.run(run())


def test_collect_deduplicates_persists_and_never_enters_trade_quotes(tmp_path: Path) -> None:
    runtime = runtime_for(tmp_path)
    now = datetime.now(UTC)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=envelope(now)))
        ) as http:
            runtime.live_quotes.client = JDGoldClient(http)
            first = await runtime.live_quotes.collect_once()
            # 同一源记录即使重复拉取，也只能保存一次。
            second = await runtime.live_quotes.collect_once()
            assert first.quote_id == second.quote_id
            assert runtime.live_quotes.status().records_saved == 1

    asyncio.run(run())
    # 默认仅落 SQLite，不再在本地新建 references/*.jsonl 文件
    assert not list((tmp_path / "references").glob("*.jsonl")) if (tmp_path / "references").exists() else True
    assert runtime.quotes.get_latest_as_of(datetime.now(UTC)) is None
    restored = runtime_for(tmp_path).live_quotes.latest()
    assert restored is not None
    assert restored.price == 812.16
    assert len(restored.record_hash or "") == 64


def test_stale_and_future_quotes_remain_visible_as_unusable(tmp_path: Path) -> None:
    async def run() -> None:
        for offset, expected in [(-60, DataQualityState.STALE), (60, DataQualityState.ANOMALY)]:
            runtime = runtime_for(tmp_path / str(offset))
            payload = envelope(datetime.now(UTC) + timedelta(seconds=offset))
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _, payload=payload: httpx.Response(200, json=payload))
            ) as http:
                runtime.live_quotes.client = JDGoldClient(http)
                quote = await runtime.live_quotes.collect_once()
            assert quote.quality_state == expected
            assert quote.available_at is None
            latest = runtime.live_quotes.latest()
            assert latest is not None
            assert latest.quality_state == expected

    asyncio.run(run())


def test_failure_is_observable_and_next_poll_recovers(tmp_path: Path) -> None:
    runtime = runtime_for(tmp_path)
    count = 0

    async def run() -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal count
            count += 1
            return httpx.Response(503) if count == 1 else httpx.Response(200, json=envelope(datetime.now(UTC)))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            runtime.live_quotes.client = JDGoldClient(http)
            with pytest.raises(JDGoldError):
                await runtime.live_quotes.collect_once()
            assert "503" in (runtime.live_quotes.status().last_error or "")
            assert runtime.live_quotes.latest() is None
            await runtime.live_quotes.collect_once()
            assert runtime.live_quotes.status().last_error is None
            assert runtime.live_quotes.status().last_success_at is not None

    asyncio.run(run())


def test_live_routes_and_disabled_mode(tmp_path: Path) -> None:
    runtime = runtime_for(tmp_path, enabled=False)
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/quotes/live/status").json()["enabled"] is False
            assert client.get("/api/v1/quotes/live/latest").status_code == 404
            assert client.post("/api/v1/quotes/live/collect").status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_lifespan_runs_collection_and_closes_connections(tmp_path: Path) -> None:
    runtime = runtime_for(tmp_path)
    payload = envelope(datetime.now(UTC))
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    runtime.live_quotes.client = JDGoldClient(http)
    previous = app.state.runtime
    app.state.runtime = runtime
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/quotes/live/collect")
            assert response.status_code == 200
            status = client.get("/api/v1/quotes/live/status").json()
            assert status["running"] is True
            assert status["records_saved"] == 1
            assert client.get("/api/v1/quotes/live/latest").json()["price"] == 812.16
            assert client.get("/api/v1/quotes/latest").status_code == 404
        assert runtime.live_quotes.status().running is False
        assert runtime.jd_http.is_closed
    finally:
        app.state.runtime = previous
        asyncio.run(http.aclose())


def test_background_poll_recovers_without_restart(tmp_path: Path) -> None:
    runtime = runtime_for(tmp_path)
    completed = asyncio.Event()
    count = 0

    async def run() -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal count
            count += 1
            if count == 1:
                return httpx.Response(503)
            completed.set()
            return httpx.Response(200, json=envelope(datetime.now(UTC)))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            runtime.live_quotes.client = JDGoldClient(http)
            runtime.live_quotes._interval = 0.01
            await runtime.live_quotes.start()
            try:
                await asyncio.wait_for(completed.wait(), timeout=1)
                # 等待存储完成，再读状态，避免把请求完成误当成采集完成。
                async with runtime.live_quotes._product_locks["CMBC-JCJ"]:
                    assert runtime.live_quotes.status().records_saved == 1
                    assert runtime.live_quotes.status().failures == 1
            finally:
                await runtime.live_quotes.stop()
            assert not runtime.live_quotes.status().running

    asyncio.run(run())


def test_log_tampering_is_rejected_after_restart(tmp_path: Path) -> None:
    import sqlite3

    test_collect_deduplicates_persists_and_never_enters_trade_quotes(tmp_path)
    db_path = tmp_path / "quant-agentic.sqlite3"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT id, raw_json FROM reference_quotes LIMIT 1").fetchone()
        assert row is not None
        tampered = str(row[1]).replace("812.16", "999.0")
        conn.execute("UPDATE reference_quotes SET raw_json = ? WHERE id = ?", (tampered, row[0]))
        conn.commit()
    with pytest.raises(ValueError, match="完整性"):
        runtime_for(tmp_path)


def test_latest_expires_without_rewriting_audit_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    test_collect_deduplicates_persists_and_never_enters_trade_quotes(tmp_path)
    runtime = runtime_for(tmp_path)
    db_path = tmp_path / "quant-agentic.sqlite3"
    with sqlite3.connect(db_path) as conn:
        original = conn.execute("SELECT raw_json FROM reference_quotes LIMIT 1").fetchone()[0]
    later = datetime.now(UTC) + timedelta(seconds=60)

    class LaterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return later

    monkeypatch.setattr("src.services.market.live_quotes.datetime", LaterClock)
    quote = runtime.live_quotes.latest()
    assert quote is not None
    assert quote.quality_state == DataQualityState.STALE
    assert quote.available_at is None
    assert quote.record_hash is None
    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT raw_json FROM reference_quotes LIMIT 1").fetchone()[0]
    assert after == original


def test_api_reports_upstream_failure_without_persisting_quote(tmp_path: Path) -> None:
    runtime = runtime_for(tmp_path)
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(403, text="secret")))
    runtime.live_quotes.client = JDGoldClient(http)
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/quotes/live/collect")
            assert response.status_code == 502
            assert "secret" not in response.text
            status = client.get("/api/v1/quotes/live/status").json()
            assert "403" in status["last_error"]
            assert status["records_saved"] == 0
    finally:
        app.dependency_overrides.clear()
        asyncio.run(http.aclose())


@pytest.mark.parametrize("field", ["jd_timeout_seconds", "live_quotes_interval_seconds"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_live_config_rejects_invalid_timing(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        Settings.model_validate({field: value})
