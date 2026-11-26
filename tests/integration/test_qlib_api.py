"""Qlib REST API 集成测试：状态查询、数据导出、路径安全防护与降级错误码。"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.configs.settings import Settings
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState
from src.contracts.exceptions import QlibUnavailableError
from src.routers.v1 import api_v1_router
from src.runtime.dependencies import RuntimeDependencies


def _build_client(tmp_path: Path) -> tuple[TestClient, RuntimeDependencies]:
    settings = Settings(
        data_dir=str(tmp_path),
        timesfm_enabled=False,
        news_enabled=False,
        live_quotes_enabled=False,
        market_bars_enabled=False,
        news_sync_enabled=False,
        jd_holdings_sync_enabled=False,
        jd_proactive_auth_enabled=False,
        qlib_provider_uri=str(tmp_path / "qlib_data"),
        qlib_experiment_dir=str(tmp_path / "qlib_experiments"),
    )
    runtime = RuntimeDependencies(settings)
    app = FastAPI()
    app.state.runtime = runtime
    app.include_router(api_v1_router)
    return TestClient(app), runtime


def test_qlib_status_export_experiments_and_security(tmp_path: Path, monkeypatch: object) -> None:
    """验证 /api/v1/qlib/* 与 /api/v1/models/status 端点及路径穿越防护与 503 降级。"""
    client, runtime = _build_client(tmp_path)

    # 写入测试双边报价（周一交易时段）
    base_dt = datetime(2026, 11, 23, 2, 0, tzinfo=UTC)
    for idx in range(4):
        dt = base_dt + timedelta(hours=idx)
        runtime.quotes.save_quote(
            QuoteRaw(
                quote_id=f"api_q_{idx}",
                source_id="CMBC",
                product_code="GOLD_ACCUMULATION",
                bid_price=620.0 + idx,
                ask_price=621.0 + idx,
                quote_at=dt,
                received_at=dt,
                available_at=dt,
                is_tradable=True,
                is_synthetic=False,
                payload_hash=f"api_h_{idx}",
                quality_state=DataQualityState.NORMAL,
            )
        )

    # 1. 状态查询与 /models/status 联动
    res_status = client.get("/api/v1/qlib/status")
    assert res_status.status_code == 200
    assert "pyqlib_installed" in res_status.json()

    res_models = client.get("/api/v1/models/status")
    assert res_models.status_code == 200
    assert "qlib" in res_models.json()

    # 2. 路径穿越拦截
    res_traversal = client.post(
        "/api/v1/qlib/export",
        json={"source_mode": "quotes", "output_dir": "../../outside_dir"},
    )
    assert res_traversal.status_code == 400

    # 3. 合法导出
    res_export = client.post(
        "/api/v1/qlib/export",
        json={"source_mode": "quotes", "instruments": ["GOLD_ACCUMULATION"], "freq": "1h", "output_dir": "qlib_data"},
    )
    assert res_export.status_code == 200
    export_body = res_export.json()
    assert export_body["manifest"]["calendar_count"] == 4

    # 4. 模拟未安装 pyqlib 时调用因子评估应返回 503
    def _raise_unavailable(*_args: object, **_kwargs: object) -> object:
        raise QlibUnavailableError("未安装可选依赖 pyqlib")

    monkeypatch.setattr(runtime.qlib_research, "evaluate_factors", _raise_unavailable)  # type: ignore[attr-defined]
    res_eval = client.post("/api/v1/qlib/factors/evaluate", json={"source_type": "local_sqlite", "freq": "1h"})
    assert res_eval.status_code == 503

    # 5. 实验列表接口
    res_exps = client.get("/api/v1/qlib/experiments")
    assert res_exps.status_code == 200
    assert res_exps.json()["total"] == 0
    asyncio.run(runtime.close())
