"""FastAPI API 路由集成单元测试。"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


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
