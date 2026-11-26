"""新增 App 级业务路由（backtest / paper / live / jd / quotes quality / alerts execute）单元测试。"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app
from src.clients.jd_gold import JDGoldError
from src.configs.settings import Settings
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState
from src.runtime.dependencies import RuntimeDependencies

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path: Path) -> Iterator[RuntimeDependencies]:
    runtime = RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            timesfm_enabled=False,
            news_enabled=False,
            live_quotes_enabled=False,
            jd_proactive_auth_enabled=False,
        )
    )
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        yield runtime
    finally:
        app.dependency_overrides.clear()
        asyncio.run(runtime.close())


def _seed_quote(runtime: RuntimeDependencies, quote_id: str = "app_q_1") -> None:
    now = datetime.now(UTC)
    runtime.quotes.save_quote(
        QuoteRaw(
            quote_id=quote_id,
            source_id="test_bank",
            bid_price=600.0,
            ask_price=600.4,
            quote_at=now,
            received_at=now,
            available_at=now,
            is_tradable=True,
            quality_state=DataQualityState.NORMAL,
            product_code="GOLD_ACCUMULATION",
        )
    )


def test_backtest_and_feasibility_routes() -> None:
    bt_resp = client.post("/api/v1/backtest/run", json={})
    assert bt_resp.status_code == 200
    bt_data = bt_resp.json()
    assert bt_data["product_code"] == "GOLD_ACCUMULATION"
    assert bt_data["quote_count"] == 0

    feas_resp = client.post("/api/v1/backtest/feasibility", json={"annual_volatility": 0.17})
    assert feas_resp.status_code == 200
    feas_data = feas_resp.json()
    assert feas_data["annual_volatility"] == 0.17
    assert len(feas_data["rows"]) > 0
    assert isinstance(feas_data["has_viable_period"], bool)


def test_paper_routes_local_and_jd_guard(isolated_runtime: RuntimeDependencies) -> None:
    _seed_quote(isolated_runtime)

    status_resp = client.get("/api/v1/paper/status?engine=local")
    assert status_resp.status_code == 200
    assert status_resp.json()["engine"] == "local"

    step_resp = client.post("/api/v1/paper/step", json={})
    assert step_resp.status_code == 200
    assert step_resp.json()["product_code"] == "GOLD_ACCUMULATION"

    buy_resp = client.post(
        "/api/v1/paper/orders",
        json={"engine": "local", "direction": "BUY", "grams": 2.0},
    )
    assert buy_resp.status_code == 200
    assert buy_resp.json()["fill"]["is_filled"] is True

    # 京东模拟盘未传 confirm=true 返回 400
    jd_guard_resp = client.post(
        "/api/v1/paper/orders",
        json={"engine": "jd", "direction": "BUY", "grams": 1.0, "confirm": False},
    )
    assert jd_guard_resp.status_code == 400
    assert "显式确认" in jd_guard_resp.json()["detail"]


def test_live_routes_control_and_batch_orders(isolated_runtime: RuntimeDependencies) -> None:
    _seed_quote(isolated_runtime)

    status_resp = client.get("/api/v1/live/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["risk_state"] == "NORMAL"

    step_resp = client.post("/api/v1/live/step", json={})
    assert step_resp.status_code == 200
    assert step_resp.json()["kill_switch_active"] is False

    halt_resp = client.post("/api/v1/live/halt", json={"reason": "UNIT_TEST_HALT"})
    assert halt_resp.status_code == 200
    assert halt_resp.json()["kill_switch_active"] is True

    resume_resp = client.post("/api/v1/live/resume")
    assert resume_resp.status_code == 200
    assert resume_resp.json()["kill_switch_active"] is False

    reg_resp = client.post(
        "/api/v1/live/batch-orders",
        json={
            "instruction_id": "batch_api_1",
            "direction": "BUY",
            "grams": 2.0,
            "limit_price": 605.0,
            "target_open": "2026-11-30T01:00:00+00:00",
        },
    )
    assert reg_resp.status_code == 200
    assert reg_resp.json()["instruction_id"] == "batch_api_1"

    exec_resp = client.post(
        "/api/v1/live/batch-orders/batch_api_1/execute",
        json={"bank_batch_price": 602.0},
    )
    assert exec_resp.status_code == 200
    assert exec_resp.json()["filled"] is True


def test_quotes_quality_and_alert_execute_with_fill(isolated_runtime: RuntimeDependencies) -> None:
    _seed_quote(isolated_runtime)

    qual_resp = client.get("/api/v1/quotes/quality?product_code=GOLD_ACCUMULATION")
    assert qual_resp.status_code == 200
    assert qual_resp.json()["total_records"] == 1

    # 构造一条待执行提醒并验证带成交参数回填同步记账
    from src.contracts.domains.decision import Decision
    from src.contracts.enums.trading import DecisionAction, HoldingPeriod

    now = datetime.now(UTC)
    quote = isolated_runtime.quotes.get_latest_as_of(as_of=now, product_code="GOLD_ACCUMULATION")
    assert quote is not None
    decision = Decision(
        decision_id="dec_alert_test",
        decision_at=now,
        action=DecisionAction.BUY,
        delta_g=2.0,
        target_inventory_g=2.0,
        horizon_primary=HoldingPeriod.W1,
    )
    alert = isolated_runtime.alerts.create_alert(
        decision=decision,
        quote=quote,
        account=isolated_runtime.ledger.take_snapshot(now),
    )
    assert alert is not None
    assert client.post(f"/api/v1/alerts/{alert.alert_id}/confirm").status_code == 200

    exec_resp = client.post(
        f"/api/v1/alerts/{alert.alert_id}/execute",
        json={"filled_grams": 2.0, "filled_price": 600.4, "fee": 0.0, "bank_order_id": "BANK_001"},
    )
    assert exec_resp.status_code == 200
    assert exec_resp.json()["state"] == "USER_EXECUTED"
    assert isolated_runtime.ledger.take_snapshot().gold_total_g == pytest.approx(2.0)


def test_jd_routes_forwarding_and_error_mapping() -> None:
    with (
        patch(
            "src.services.jd_gold.trade.JDGoldTradeService.get_holdings",
            new=AsyncMock(side_effect=AssertionError("刷新访问了上游持仓")),
        ) as upstream_holdings,
        patch(
            "src.services.jd_gold.trade.JDGoldTradeService.list_conditional_orders",
            new=AsyncMock(return_value={"items": []}),
        ) as mock_cond,
        patch(
            "src.services.jd_gold.analysis.JDGoldAnalysisService.get_opportunities",
            new=AsyncMock(return_value={"signals": []}),
        ),
    ):
        assert client.get("/api/v1/jd/holdings").status_code == 401
        assert client.get("/api/v1/jd/holdings/snapshot").json()["status"] == "auth_required"
        upstream_holdings.assert_not_awaited()
        cond_resp = client.get("/api/v1/jd/conditional-orders?start_date=2026-11-01&end_date=2026-11-24")
        assert cond_resp.status_code == 200
        assert mock_cond.await_count == 1
        assert client.get("/api/v1/jd/analysis/opportunities").json() == {"signals": []}

    # 上游业务异常应映射为 502
    with patch(
        "src.services.jd_gold.analysis.JDGoldAnalysisService.get_order_book",
        new=AsyncMock(side_effect=JDGoldError("UPSTREAM_ERROR", "error")),
    ):
        resp_502 = client.get("/api/v1/jd/analysis/orderbook")
        assert resp_502.status_code == 502
