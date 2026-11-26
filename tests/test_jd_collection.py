"""京东增量采集与前端 SQLite 只读接口回归测试。"""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from main import app
from src.agents.news import NewsAgentError
from src.clients.jd_gold import JDGoldError
from src.configs.settings import Settings
from src.contracts.domains.event import NewsEvent
from src.contracts.domains.jd_auth import JDGoldAuthState
from src.contracts.domains.reference_quote import ReferenceQuote
from src.repositories.jd_collection import JDCollectionRepository
from src.repositories.jd_gold_auth import JDGoldAuthRepository
from src.repositories.reference_quote import ReferenceQuoteRepository
from src.runtime.dependencies import RuntimeDependencies


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=str(tmp_path),
        timesfm_enabled=False,
        news_enabled=False,
        news_sync_enabled=False,
        live_quotes_enabled=False,
        market_bars_enabled=False,
        jd_holdings_sync_enabled=False,
        jd_proactive_auth_enabled=False,
        jd_access_token=SecretStr(""),
    )


def _article(article_id: str) -> dict[str, str]:
    return {"id": article_id, "title": f"快讯 {article_id}", "content": f"正文 {article_id}"}


def test_flash_failed_page_is_retried_after_restart(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    market = "src.services.jd_gold.market.JDGoldMarketService"

    async def run() -> None:
        initial = RuntimeDependencies(settings)
        store = JDCollectionRepository(settings.database_path)
        with (
            patch(f"{market}.get_news_flash", new=AsyncMock(return_value=[_article("1")])),
            patch(f"{market}.get_news", new=AsyncMock(return_value=[])),
        ):
            await initial.news_sync.sync_once(page_size=2)
        assert store.checkpoint("flash") == "flash:jd_flash_1"
        with (
            patch(
                f"{market}.get_news_flash",
                new=AsyncMock(side_effect=[[_article("4"), _article("3")], JDGoldError("UPSTREAM_ERROR")]),
            ),
            patch(f"{market}.get_news", new=AsyncMock(return_value=[])),
        ):
            await initial.news_sync.sync_once(page_size=2)
        assert store.checkpoint("flash") == "flash:jd_flash_1"
        assert len(store.list_articles(limit=10)) == 3
        assert store.flash_scan() == ("flash:jd_flash_4", "3")
        await initial.close()

        resumed = RuntimeDependencies(settings)
        try:
            with patch(
                f"{market}.get_news_flash",
                new=AsyncMock(return_value=[_article("2"), _article("1")]),
            ) as flash:
                await resumed.news_sync.sync_once(page_size=2)
                assert [call.kwargs["last_id"] for call in flash.await_args_list] == ["3"]
            assert store.checkpoint("flash") == "flash:jd_flash_4"
            assert store.flash_scan() is None
            assert len(store.list_articles(limit=10)) == 4
        finally:
            await resumed.close()

    asyncio.run(run())


def test_workspace_reads_committed_data_and_keeps_holdings_when_source_fails(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    auth_store = JDGoldAuthRepository(settings.database_path, scope=settings.jd_bff_base_url)
    authorized = JDGoldAuthState(access_token=SecretStr("test-account"))
    auth_store.save(authorized)
    holdings = {"totalGramAll": 0, "avgCostPrice": None, "holdingList": []}

    async def seed() -> None:
        runtime = RuntimeDependencies(settings)
        try:
            target = "src.services.jd_gold.trade.JDGoldTradeService.get_holdings"
            with patch(target, new=AsyncMock(return_value=holdings)):
                await runtime.jd_holdings.collect_once()
                await runtime.jd_holdings.collect_once()
            with patch(target, new=AsyncMock(return_value={"totalGramAll": 0})), pytest.raises(ValidationError):
                await runtime.jd_holdings.collect_once()
            with patch(target, new=AsyncMock(side_effect=JDGoldError("UPSTREAM_ERROR"))), pytest.raises(JDGoldError):
                await runtime.jd_holdings.collect_once()
            assert runtime.jd_holdings.latest().status == "fresh"
            with patch(
                "src.services.jd_gold.market.JDGoldMarketService.get_news_flash",
                new=AsyncMock(return_value=[_article("1")]),
            ):
                await runtime.news_sync.sync_once()
        finally:
            await runtime.close()

    asyncio.run(seed())
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT count(*) FROM jd_collection_snapshots").fetchone() == (1,)

    runtime = RuntimeDependencies(settings)
    # 在容器初始化之后写行情，验证页面从 SQLite 读取而不是使用启动缓存。
    now = datetime.now(UTC)
    ReferenceQuoteRepository(settings.resolved_data_dir / "references", database_path=settings.database_path).save(
        ReferenceQuote(
            quote_id="jd-offline",
            product_code="CMBC-JCJ",
            price=600,
            quote_at=now,
            received_at=now,
            available_at=now,
            payload_hash="b" * 64,
        )
    )
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        with (
            patch.object(
                httpx.AsyncClient, "request", new=AsyncMock(side_effect=AssertionError("前端刷新访问了外部服务"))
            ) as upstream,
            patch.object(app.state, "runtime", runtime),
            TestClient(app) as client,
        ):
            auth_store.save(JDGoldAuthState(expired_token_fingerprint=authorized.credential_fingerprint))
            for _ in range(2):
                for path in (
                    "/api/v1/live/status",
                    "/api/v1/models/status",
                    "/api/v1/quotes/live/products",
                    "/api/v1/alerts?limit=30",
                    "/api/v1/reconciliation/reports?limit=15",
                    "/api/v1/ledger/orders?limit=200",
                ):
                    assert client.get(path).status_code == 200
                result = client.get("/api/v1/jd/holdings/snapshot").json()
                assert result["status"] == "auth_required"
                assert result["data"]["holdingList"] == []
                assert result["last_success_at"]
                assert client.get("/api/v1/jd/holdings").json()["totalGramAll"] == 0
                assert client.get("/api/v1/quotes/live/latest").json()["quote_id"] == "jd-offline"
                assert any(
                    quote["latest"] and quote["latest"]["quote_id"] == "jd-offline"
                    for quote in client.get("/api/v1/quotes/live/status/all").json()
                )
                assert client.get("/api/v1/models/news/overview").json()["jd_articles"][0]["title"] == "快讯 1"
                assert client.get("/api/v1/jd/news?channel=flash").json()[0]["title"] == "快讯 1"
            auth_store.save(JDGoldAuthState(access_token=SecretStr("other-account")))
            assert client.get("/api/v1/jd/holdings/snapshot").json()["status"] == "pending"
            assert client.get("/api/v1/jd/holdings").status_code == 404
            upstream.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()
        asyncio.run(runtime.close())


def test_collectors_lock_data_dir_until_shutdown(tmp_path: Path) -> None:
    async def run() -> None:
        settings = _settings(tmp_path)
        settings.jd_holdings_sync_enabled = True
        first = RuntimeDependencies(settings)
        second = RuntimeDependencies(settings)
        try:
            async with first.background_collection():
                with pytest.raises(RuntimeError, match="已有京东采集进程"):
                    async with second.background_collection():
                        pass
            async with second.background_collection():
                assert second.jd_holdings.latest().status == "auth_required"
        finally:
            await first.close()
            await second.close()

    asyncio.run(run())


def test_stale_holdings_and_empty_success(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    auth_store = JDGoldAuthRepository(settings.database_path, scope=settings.jd_bff_base_url)
    state = JDGoldAuthState(access_token=SecretStr("test-account"))
    auth_store.save(state)
    store = JDCollectionRepository(settings.database_path)
    scope = f"holdings:{state.credential_fingerprint}"
    store.save_snapshot(
        scope,
        {"totalGramAll": 10, "avgCostPrice": 600, "holdingList": []},
        datetime.now(UTC) - timedelta(days=1),
    )

    async def run() -> None:
        runtime = RuntimeDependencies(settings)
        try:
            assert runtime.jd_holdings.latest().status == "stale"
            with patch(
                "src.services.jd_gold.trade.JDGoldTradeService.get_holdings",
                new=AsyncMock(return_value={"totalGramAll": 0, "holdingList": []}),
            ):
                await runtime.jd_holdings.collect_once()
            result = runtime.jd_holdings.latest()
            assert result.status == "fresh"
            assert result.data is not None
            assert result.data["totalGramAll"] == 0
        finally:
            await runtime.close()

    asyncio.run(run())


def test_news_raw_survives_model_failure_and_retries_from_database(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = JDCollectionRepository(settings.database_path)
    now = datetime.now(UTC)
    event = NewsEvent(
        event_id="jd_flash_1",
        source="JD_FLASH",
        title="快讯 1",
        content="正文 1",
        published_at=now,
        available_at=now,
        event_type="GENERAL",
        severity=0.2,
        confidence=0.9,
        summary="摘要",
    )
    market = "src.services.jd_gold.market.JDGoldMarketService"

    async def run() -> None:
        initial = RuntimeDependencies(settings)
        try:
            failed_model = AsyncMock()
            failed_model.extract.side_effect = NewsAgentError("模型暂不可用")
            with (
                patch.object(initial.news_sync, "_news_agent", failed_model),
                patch(f"{market}.get_news_flash", new=AsyncMock(return_value=[_article("1")])),
            ):
                assert (await initial.news_sync.sync_once()).failed_count == 1
            assert store.list_articles(limit=1)[0].event_id == "jd_flash_1"
            assert len(store.pending_articles(5)) == 1
        finally:
            await initial.close()

        resumed = RuntimeDependencies(settings)
        try:
            healthy_model = AsyncMock()
            healthy_model.extract.return_value = event
            with (
                patch.object(resumed.news_sync, "_news_agent", healthy_model),
                patch(f"{market}.get_news_flash", new=AsyncMock(side_effect=JDGoldError("UPSTREAM_ERROR"))),
                patch(f"{market}.get_news", new=AsyncMock(side_effect=JDGoldError("UPSTREAM_ERROR"))),
            ):
                assert (await resumed.news_sync.sync_once()).ingested_count == 1
            assert store.pending_articles(5) == []
            assert resumed.news.stored_overview(now, 10)[3][0].event_id == "jd_flash_1"
        finally:
            await resumed.close()

    asyncio.run(run())
