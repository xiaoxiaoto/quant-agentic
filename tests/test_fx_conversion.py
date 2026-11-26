"""USD/oz 国际贵金属实时汇率换算 CNY/g 回归测试。"""

import asyncio
from pathlib import Path

import httpx

from src.clients.fx_rate import FXRateClient
from src.clients.jd_gold import JDGoldClient
from src.repositories.jd_collection import JDCollectionRepository
from src.repositories.reference_quote import ReferenceQuoteRepository
from src.services.market.live_quotes import LiveQuoteCollector


def test_fx_rate_client_primary_fallback_and_cache() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "primary" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200, json={"rates": {"CNY": 6.68}})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            fx = FXRateClient(
                http,
                ttl_seconds=300.0,
                primary_url="https://primary.test/usd",
                fallback_url="https://fallback.test/usd",
            )
            first = await fx.get_usd_cny()
            second = await fx.get_usd_cny()
            assert first == 6.68
            assert second == 6.68
            assert fx.cached_usd_cny() == 6.68
            # 第二次命中缓存，不再请求上游
            assert len(calls) == 2

    asyncio.run(run())


def test_live_quote_status_converts_usd_oz_and_cny_kg_to_cny_per_g(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "fx.test" in str(request.url):
            return httpx.Response(200, json={"rates": {"CNY": 6.68}})
        code = request.url.params.get("uniqueCode", "")
        if code == "WG-XAUUSD":
            return httpx.Response(
                200,
                json={"code": 200, "data": {"uniqueCode": "WG-XAUUSD", "lastPrice": "3621.84", "tradeDateTime": "{}"}},
            )
        return httpx.Response(
            200,
            json={
                "success": True,
                "resultCode": 0,
                "resultData": {
                    "data": {
                        "stockCode": "Ag(T+D)",
                        "currentPrice": "9500",
                        "currentTime": "1795507960000",
                    }
                },
            },
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            fx = FXRateClient(http, primary_url="https://fx.test/usd", fallback_url="https://fx.test/usd")
            store = JDCollectionRepository(tmp_path / "quant-agentic.sqlite3")
            repository = ReferenceQuoteRepository(tmp_path)
            collector = LiveQuoteCollector(
                JDGoldClient(http),
                repository,
                enabled=True,
                interval_seconds=60,
                stale_seconds=30,
                fx_client=fx,
                fx_repository=store,
            )
            await collector.collect_once("WG-XAUUSD")
            st_xau = collector.status("WG-XAUUSD")
            assert st_xau.latest is not None
            assert st_xau.latest.unit == "USD/oz"
            assert st_xau.fx_rate_usd_cny == 6.68
            assert st_xau.cny_per_g == round(3621.84 * 6.68 / 31.1034768, 2)

            reopened = LiveQuoteCollector(
                JDGoldClient(http),
                repository,
                enabled=False,
                interval_seconds=60,
                stale_seconds=30,
                fx_repository=store,
            )
            assert reopened.status("WG-XAUUSD").cny_per_g == st_xau.cny_per_g

            await collector.collect_once("SGE-Ag(T+D)")
            st_ag = collector.status("SGE-Ag(T+D)")
            assert st_ag.cny_per_g == 9.5
            assert st_ag.fx_rate_usd_cny is None

    asyncio.run(run())
