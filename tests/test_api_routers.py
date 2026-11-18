"""FastAPI API 路由集成单元测试。"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from src.configs.settings import Settings
from src.runtime.dependencies import RuntimeDependencies

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path: Path) -> Iterator[None]:
    runtime = RuntimeDependencies(
        Settings.model_construct(data_dir=str(tmp_path), timesfm_enabled=False, news_llm_enabled=False)
    )
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    yield
    app.dependency_overrides.clear()


def test_health_check() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_quote_api_record_and_get() -> None:
    now_str = datetime.now(UTC).isoformat()
    payload = {
        "quote_id": "api_q_01",
        "source_id": "test_src",
        "bid_price": 605.0,
        "ask_price": 605.5,
        "quote_at": now_str,
        "is_tradable": True,
        "quality_state": "NORMAL",
        "product_code": "GOLD_ACCUMULATION",
    }
    resp_post = client.post("/api/v1/quotes", json=payload)
    assert resp_post.status_code == 200
    data = resp_post.json()
    assert data["quote_id"] == "api_q_01"
    assert data["bid_price"] == 605.0

    resp_latest = client.get("/api/v1/quotes/latest")
    assert resp_latest.status_code == 200
    assert resp_latest.json()["quote_id"] == "api_q_01"


def test_decisions_evaluate_api() -> None:
    now_str = datetime.now(UTC).isoformat()
    payload = {
        "quote": {
            "quote_id": "api_dec_q",
            "source_id": "test_src",
            "bid_price": 600.0,
            "ask_price": 600.5,
            "quote_at": now_str,
            "received_at": now_str,
            "is_tradable": True,
            "quality_state": "NORMAL",
            "product_code": "GOLD_ACCUMULATION",
        },
        "target_buy_grams": 1.0,
    }
    resp = client.post("/api/v1/decisions/evaluate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "raw_decision" in data
    assert "approved_decision" in data
    assert "action" in data["approved_decision"]


def test_ledger_snapshot_and_orders_api() -> None:
    resp_snap = client.get("/api/v1/ledger/snapshot")
    assert resp_snap.status_code == 200
    snap = resp_snap.json()
    assert "cash_available" in snap
    assert "gold_total_g" in snap

    resp_orders = client.get("/api/v1/ledger/orders")
    assert resp_orders.status_code == 200
    assert isinstance(resp_orders.json(), list)


def test_reconciliation_snapshot_api() -> None:
    payload = {
        "bank_cash": 100000.0,
        "bank_gold_g": 0.0,
        "bank_cost_basis": None,
    }
    resp = client.post("/api/v1/reconciliation/snapshot", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "report" in data
    assert "recommended_risk_state" in data
    assert data["report"]["matched"] is True


def test_quote_cannot_claim_normal_for_bad_prices() -> None:
    payload = {
        "quote_id": "api_bad_price",
        "source_id": "test_src",
        "bid_price": -1.0,
        "ask_price": 600.0,
        "quote_at": datetime.now(UTC).isoformat(),
        "quality_state": "NORMAL",
    }
    response = client.post("/api/v1/quotes", json=payload)
    assert response.status_code == 200
    assert response.json()["quality_state"] == "ANOMALY"
    assert response.json()["available_at"] is None


def test_quotes_require_timezone() -> None:
    response = client.post(
        "/api/v1/quotes",
        json={
            "quote_id": "naive",
            "source_id": "test_src",
            "bid_price": 600,
            "ask_price": 601,
            "quote_at": "2026-11-18T10:00:00",
        },
    )
    assert response.status_code == 422


def test_latest_query_requires_timezone() -> None:
    assert client.get("/api/v1/quotes/latest?as_of=2026-11-18T10:00:00").status_code == 422


def test_decisions_reject_nonpositive_quantity() -> None:
    now = datetime.now(UTC).isoformat()
    response = client.post(
        "/api/v1/decisions/evaluate",
        json={
            "quote": {
                "quote_id": "invalid_qty",
                "source_id": "test_src",
                "bid_price": 600,
                "ask_price": 601,
                "quote_at": now,
                "received_at": now,
                "is_tradable": True,
            },
            "target_buy_grams": -1,
        },
    )
    assert response.status_code == 422


def test_routers_share_one_ledger() -> None:
    runtime = app.dependency_overrides[RuntimeDependencies.from_request]()
    runtime.ledger.record_buy("shared_buy", grams=2, price=600)
    snapshot = client.get("/api/v1/ledger/snapshot").json()
    assert snapshot["gold_total_g"] == 2
    assert len(client.get("/api/v1/ledger/orders").json()) == 1
    response = client.post(
        "/api/v1/reconciliation/snapshot",
        json={
            "bank_cash": snapshot["cash_available"],
            "bank_gold_g": 2,
            "bank_cost_basis": 600,
        },
    )
    assert response.json()["report"]["matched"] is True
    now = datetime.now(UTC).isoformat()
    decision = client.post(
        "/api/v1/decisions/evaluate",
        json={
            "quote": {
                "quote_id": "held",
                "source_id": "test_src",
                "bid_price": 600,
                "ask_price": 601,
                "quote_at": now,
                "received_at": now,
                "is_tradable": False,
            },
        },
    )
    assert decision.json()["raw_decision"]["target_inventory_g"] == 2


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity"])
def test_quotes_reject_nonfinite_prices(price: str) -> None:
    response = client.post(
        "/api/v1/quotes",
        json={
            "quote_id": "nonfinite",
            "source_id": "test_src",
            "bid_price": price,
            "ask_price": 601,
            "quote_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 422


def test_quote_gate_detects_duplicates_and_future_times() -> None:
    payload = {
        "quote_id": "duplicate",
        "source_id": "test_src",
        "bid_price": 600,
        "ask_price": 600.5,
        "quote_at": datetime.now(UTC).isoformat(),
    }
    assert client.post("/api/v1/quotes", json=payload).json()["quality_state"] == "NORMAL"
    duplicate = client.post("/api/v1/quotes", json=payload).json()
    assert duplicate["quality_state"] == "ANOMALY"
    assert duplicate["available_at"] is None
    payload["quote_id"] = "future"
    payload["quote_at"] = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    assert client.post("/api/v1/quotes", json=payload).json()["quality_state"] == "ANOMALY"
    assert client.get("/api/v1/quotes/latest").json()["quote_id"] == "duplicate"


@pytest.mark.parametrize(
    ("field", "value"),
    [("bid_price", 0), ("ask_price", -1), ("received_at", "2026-11-18T10:00:00"), ("ask_price", "NaN")],
)
def test_decision_quote_constraints(field: str, value: object) -> None:
    now = datetime.now(UTC).isoformat()
    quote = {
        "quote_id": "invalid",
        "source_id": "test_src",
        "bid_price": 600,
        "ask_price": 601,
        "quote_at": now,
        "received_at": now,
        "is_tradable": True,
    }
    quote[field] = value
    assert client.post("/api/v1/decisions/evaluate", json={"quote": quote}).status_code == 422


def test_snapshot_rejects_nonfinite_balances() -> None:
    response = client.post(
        "/api/v1/reconciliation/snapshot",
        json={
            "bank_cash": "NaN",
            "bank_gold_g": 0,
        },
    )
    assert response.status_code == 422


def test_overflowing_json_number_returns_validation_error() -> None:
    response = client.post(
        "/api/v1/quotes",
        content='{"quote_id":"overflow","source_id":"test","bid_price":1e400,'
        '"ask_price":601,"quote_at":"2026-11-18T10:00:00+08:00"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422


def test_runtime_applies_configured_fees_and_quality_threshold(tmp_path: Path) -> None:
    runtime = app.dependency_overrides[RuntimeDependencies.from_request]()
    runtime.settings.spread_anomaly_threshold = 0.0001
    response = client.post(
        "/api/v1/quotes",
        json={
            "quote_id": "wide",
            "source_id": "test",
            "bid_price": 600,
            "ask_price": 601,
            "quote_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.json()["quality_state"] == "ANOMALY"
    configured = RuntimeDependencies(
        Settings.model_construct(
            data_dir=str(tmp_path / "configured"),
            timesfm_enabled=False,
            news_llm_enabled=False,
            buy_fee_rate=0.01,
            sell_fee_rate=0.02,
        )
    )
    configured.ledger.record_buy("fee_buy", grams=1, price=600)
    configured.ledger.record_sell("fee_sell", grams=1, price=600)
    assert configured.ledger.take_snapshot().cash_available == 99982


@pytest.mark.parametrize("field", ["requested_grams", "execution_price", "filled_grams"])
def test_order_reconciliation_rejects_invalid_numbers(field: str) -> None:
    order: dict[str, object] = {
        "client_order_id": "bad",
        "direction": "BUY",
        "requested_grams": 1,
        "execution_price": 600,
        "filled_grams": 1,
    }
    order[field] = "NaN"
    response = client.post(
        "/api/v1/reconciliation/orders",
        json={
            "local_orders": [order],
            "bank_statements": [],
        },
    )
    assert response.status_code == 422
