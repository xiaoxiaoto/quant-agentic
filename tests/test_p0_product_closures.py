"""P0 产品闭环测试：实盘参考价估算辅助模式、特征基差自动联动与京东快讯风控同步管道。"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
from src.clients.jd_gold import JDGoldClient, JDGoldError
from src.configs.settings import Settings
from src.contracts.domains.event import NewsEvent
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.reference_quote import ReferenceQuote
from src.contracts.enums.trading import DataQualityState, HoldingPeriod
from src.repositories.quote import QuoteRepository
from src.repositories.reference_quote import ReferenceQuoteRepository
from src.runtime.dependencies import RuntimeDependencies


def _runtime(tmp_path: Path, **overrides: object) -> RuntimeDependencies:
    params: dict[str, object] = {
        "data_dir": str(tmp_path),
        "timesfm_enabled": False,
        "news_enabled": False,
        "live_quotes_enabled": True,
        "live_quotes_products": ("CMBC-JCJ", "SGE-Au99.99"),
    }
    params.update(overrides)
    return RuntimeDependencies(Settings(**params))  # type: ignore[arg-type]


def _seed_cmbc_reference(runtime: RuntimeDependencies, now: datetime, price: float = 800.0) -> None:
    envelope = {
        "success": True,
        "resultCode": 0,
        "resultData": {
            "status": "SUCCESS",
            "datas": {
                "id": 91001,
                "productSku": "21001001000001",
                "demode": False,
                "price": f"{price:.2f}",
                "time": str(int(now.timestamp() * 1000)),
            },
        },
    }

    async def collect() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=envelope))
        ) as http:
            runtime.live_quotes.client = JDGoldClient(http)
            await runtime.live_quotes.collect_once("CMBC-JCJ")

    asyncio.run(collect())


def test_live_reference_assist_mode_opt_in_and_isolated(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    runtime = _runtime(
        tmp_path,
        live_reference_assist_enabled=True,
        live_reference_spread_pct=0.002,
    )
    # 先写入历史参考行情构造上涨动量，便于触发实盘辅助提醒
    ref_repo = ReferenceQuoteRepository(tmp_path / "references", database_path=runtime.settings.database_path)
    for idx, hours_ago in enumerate((200, 96, 30, 6)):
        ts = now - timedelta(hours=hours_ago)
        ref_repo.save(
            ReferenceQuote(
                quote_id=f"cmbc_hist_{idx}",
                product_code="CMBC-JCJ",
                price=720.0 + idx * 20.0,
                quote_at=ts,
                received_at=ts,
                available_at=ts,
                payload_hash=f"{idx + 1:064x}",
            )
        )
    _seed_cmbc_reference(runtime, now, price=810.0)

    res = runtime.live.evaluate_once(
        product_code="GOLD_ACCUMULATION",
        reference_product_code="CMBC-JCJ",
        as_of=now + timedelta(seconds=1),
    )
    assert res.is_reference_estimated is True
    assert res.quote_id is not None
    assert res.quote_id.startswith("ref_assist_CMBC-JCJ_")
    assert "【参考价估算辅助】" in res.message
    assert res.raw_decision is not None
    assert "REFERENCE_ESTIMATED_QUOTE" in res.raw_decision.risk_flags

    # 真实报价表不得被参考价估算报价污染
    real_repo = QuoteRepository(tmp_path, database_path=runtime.settings.database_path)
    assert real_repo.get_latest_as_of(now + timedelta(seconds=5), exclude_synthetic=False) is None

    # 若生成提醒，提醒必须显式标注参考价估算与免责声明
    if res.alert is not None:
        assert res.alert.is_reference_estimated is True
        assert "REFERENCE_ESTIMATED_QUOTE" in res.alert.risk_flags
        assert "官方实时双边报价" in res.alert.disclaimer

    # 即使开启辅助模式，显式传入合成报价仍必须严格拒绝
    synthetic = QuoteRaw(
        quote_id="syn_explicit",
        source_id="SYN",
        bid_price=799.0,
        ask_price=801.0,
        quote_at=now,
        received_at=now,
        available_at=now,
        is_tradable=True,
        is_synthetic=True,
    )
    with pytest.raises(ValueError, match="合成"):
        runtime.live.evaluate_once(quote=synthetic, as_of=now + timedelta(seconds=1))

    # 状态接口正确暴露开关
    status = runtime.live.get_status()
    assert status["live_reference_assist_enabled"] is True


def test_basis_reference_linkage_in_paper_and_live(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    ref_repo = ReferenceQuoteRepository(tmp_path / "references", database_path=tmp_path / "quant-agentic.sqlite3")
    ref_repo.save(
        ReferenceQuote(
            quote_id="sge_1",
            product_code="SGE-Au99.99",
            price=800.0,
            unit="CNY/g",
            quote_at=now,
            received_at=now,
            available_at=now,
            payload_hash="a" * 64,
        )
    )
    runtime = _runtime(tmp_path, basis_reference_product="SGE-Au99.99")

    real_quote = QuoteRaw(
        quote_id="q_real_basis",
        source_id="bank_real",
        product_code="GOLD_ACCUMULATION",
        bid_price=803.0,
        ask_price=805.0,
        quote_at=now,
        received_at=now,
        available_at=now,
        is_tradable=True,
        quality_state=DataQualityState.NORMAL,
    )
    runtime.quotes.save_quote(real_quote)

    captured_refs: list[float | None] = []
    original_extract = runtime.features.extract_features

    def spy_extract(
        history_quotes: list[QuoteRaw],
        intl_ref_cny: float | None = None,
        as_of: datetime | None = None,
        horizon: HoldingPeriod = HoldingPeriod.W1,
    ):
        captured_refs.append(intl_ref_cny)
        return original_extract(history_quotes, intl_ref_cny=intl_ref_cny, as_of=as_of, horizon=horizon)

    runtime.features.extract_features = spy_extract  # type: ignore[method-assign]

    runtime.paper.step_local(product_code="GOLD_ACCUMULATION", as_of=now + timedelta(seconds=1))
    runtime.live.evaluate_once(product_code="GOLD_ACCUMULATION", as_of=now + timedelta(seconds=1))

    assert captured_refs == [800.0, 800.0]


def test_jd_news_flash_sync_pipeline_and_deduplication(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    runtime = _runtime(tmp_path)

    # 注入模拟 NewsAgent 与京东快讯返回
    mock_agent = AsyncMock()
    mock_agent.extract = AsyncMock(
        return_value=NewsEvent(
            event_id="jd_flash_778899",
            source="财联社",
            title="突发地缘冲突升级推动避险情绪升温",
            content="中东局势升级，现货黄金短线拉升。",
            published_at=now - timedelta(minutes=5),
            available_at=now,
            event_type="GEOPOLITICAL",
            severity=0.9,
            confidence=0.85,
            summary="地缘冲突升级推升黄金避险需求",
        )
    )
    runtime.news_agent = mock_agent
    runtime.news_sync._news_agent = mock_agent

    flash_payload = {
        "success": True,
        "resultData": {
            "datas": [
                {
                    "id": "778899",
                    "title": "突发地缘冲突升级推动避险情绪升温",
                    "content": "中东局势升级，现货黄金短线拉升。",
                    "source": "财联社",
                    "publishTime": str(int((now - timedelta(minutes=5)).timestamp() * 1000)),
                }
            ]
        },
    }
    runtime.jd_gold.market.get_news_flash = AsyncMock(return_value=flash_payload)  # type: ignore[method-assign]

    # 首次同步：提取并入库，触发高危新闻风控标志
    first = asyncio.run(runtime.news_sync.sync_once(page_size=5))
    assert first.fetched_count == 1
    assert first.ingested_count == 1
    assert first.skipped_count == 0
    assert first.failed_count == 0
    assert any("NEWS_RISK_GEOPOLITICAL_jd_flash_778899" in f for f in first.triggered_risk_flags)
    assert mock_agent.extract.await_count == 1

    # 二次同步：幂等去重，不再重复调用大模型
    second = asyncio.run(runtime.news_sync.sync_once(page_size=5))
    assert second.fetched_count == 1
    assert second.ingested_count == 0
    assert second.skipped_count == 1
    assert mock_agent.extract.await_count == 1

    # API 路由验证
    app.state.runtime = runtime
    try:
        client = TestClient(app)
        resp = client.post("/api/v1/models/news/sync?page_size=5")
        assert resp.status_code == 200
        body = resp.json()
        assert body["fetched_count"] == 1
        assert body["skipped_count"] == 1

        status_resp = client.get("/api/v1/models/status")
        assert status_resp.status_code == 200
        assert "news_sync" in status_resp.json()
    finally:
        app.state.runtime = None


def test_jd_news_sync_falls_back_to_information_list_on_flash_error(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    runtime = _runtime(tmp_path)
    mock_agent = AsyncMock()
    mock_agent.extract = AsyncMock(
        return_value=NewsEvent(
            event_id="jd_flash_fallback_1",
            source="JD_GOLD_NEWS",
            title="国际金价冲高回落",
            content="美联储偏鹰压制金价。",
            published_at=now - timedelta(minutes=2),
            available_at=now,
            event_type="MACRO",
            severity=0.4,
            confidence=0.8,
            summary="金价震荡",
        )
    )
    runtime.news_agent = mock_agent
    runtime.news_sync._news_agent = mock_agent

    runtime.jd_gold.market.get_news_flash = AsyncMock(side_effect=JDGoldError("10000001", "upstream error"))  # type: ignore[method-assign]
    runtime.jd_gold.market.get_news = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "informationList": [
                {
                    "timeStamp": str(int((now - timedelta(minutes=2)).timestamp() * 1000)),
                    "title": "国际金价冲高回落",
                    "content": "美联储偏鹰压制金价。",
                }
            ]
        }
    )

    report = asyncio.run(runtime.news_sync.sync_once(page_size=3))
    assert report.fetched_count == 1
    assert report.ingested_count == 1
    assert report.failed_count == 0
    assert mock_agent.extract.await_count == 1
