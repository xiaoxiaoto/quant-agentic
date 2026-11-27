"""宏观与汇率历史日线客户端及 ExternalBarSync 同步单元测试。"""

from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from src.clients.external_bars import ExternalBarClient
from src.clients.macro_bars import MacroBarClient
from src.contracts.domains.market_bar import (
    MACRO_DXY_PRODUCT,
    MACRO_US10Y_PRODUCT,
    MACRO_USDCNY_PRODUCT,
)
from src.repositories.market_bar import MarketBarRepository
from src.services.market.external_bars import ExternalBarSync


def _mock_transport(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "api.frankfurter.dev" in url:
        return httpx.Response(
            200,
            json={
                "base": "USD",
                "rates": {
                    "2026-11-10": {"CNY": 7.20},
                    "2026-11-11": {"CNY": 7.25},
                },
            },
        )
    if "fred.stlouisfed.org" in url:
        series_id = request.url.params.get("id")
        if series_id == "DTWEXBGS":
            body = "DATE,DTWEXBGS\n2026-11-10,104.5\n2026-11-11,.\n2026-11-12,105.2\n"
            return httpx.Response(200, text=body)
        if series_id == "DGS10":
            body = "DATE,DGS10\n2026-11-10,4.25\n2026-11-11,4.31\n"
            return httpx.Response(200, text=body)
    return httpx.Response(404)


def test_macro_client_remote_and_sync_to_sqlite(tmp_path: Path) -> None:
    """验证 Frankfurter/FRED 解析、缺失值过滤、UTC 可用时间与 SQLite 幂等入库。"""
    repo = MarketBarRepository(tmp_path / "test.sqlite3")
    with httpx.Client(transport=httpx.MockTransport(_mock_transport)) as http:
        macro_client = MacroBarClient(http)
        sync = ExternalBarSync(repo, ExternalBarClient(http), macro_client=macro_client)
        reports = sync.run(["macro_daily"], start=date(2026, 11, 10), end=date(2026, 11, 15))

    assert reports[f"macro_daily/{MACRO_USDCNY_PRODUCT}/day"].error is None
    assert reports[f"macro_daily/{MACRO_DXY_PRODUCT}/day"].error is None
    assert reports[f"macro_daily/{MACRO_US10Y_PRODUCT}/day"].error is None

    fx_bars = repo.query_klines(MACRO_USDCNY_PRODUCT, "day", source="macro_daily", limit=None)
    dxy_bars = repo.query_klines(MACRO_DXY_PRODUCT, "day", source="macro_daily", limit=None)
    us10y_bars = repo.query_klines(MACRO_US10Y_PRODUCT, "day", source="macro_daily", limit=None)

    assert len(fx_bars) == 2
    assert len(dxy_bars) == 2  # 缺失标记 "." 已被过滤
    assert len(us10y_bars) == 2
    assert fx_bars[0].available_at == datetime(2026, 11, 10, 22, 0, tzinfo=UTC)


def test_macro_client_local_csv_fallback(tmp_path: Path) -> None:
    """验证配置本地 CSV 目录时优先离线读取，不依赖外部网络。"""
    csv_dir = tmp_path / "macro_csv"
    csv_dir.mkdir()
    (csv_dir / "usdcny.csv").write_text("date,close\n2026-11-10,7.18\n2026-11-11,7.22\n", encoding="utf-8")

    def _fail_transport(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("配置本地 CSV 时不应发起远端 HTTP 请求")

    with httpx.Client(transport=httpx.MockTransport(_fail_transport)) as http:
        client = MacroBarClient(http, csv_dir=csv_dir)
        rows = list(client.fetch_product(MACRO_USDCNY_PRODUCT, date(2026, 11, 10), date(2026, 11, 12)))

    assert len(rows) == 2
    assert rows[0].close == 7.18
    assert rows[1].close == 7.22
