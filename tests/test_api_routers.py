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
        Settings.model_construct(data_dir=str(tmp_path), timesfm_enabled=False, news_enabled=False)
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

    resp_fifo = client.get("/api/v1/ledger/fifo-summary")
    assert resp_fifo.status_code == 200
    fifo_data = resp_fifo.json()
    assert "open_position_cost" in fifo_data
    assert "open_grams" in fifo_data
    assert "open_lot_count" in fifo_data
    assert "items" in fifo_data

    # 非法 reference_price（非正数 / 非有限浮点）应返回 422
    for bad_price in ("0", "-10", "nan", "inf"):
        bad_resp = client.get(f"/api/v1/ledger/fifo-summary?reference_price={bad_price}")
        assert bad_resp.status_code == 422


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
            news_enabled=False,
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


def test_alerts_router_uses_isolated_runtime_dependencies() -> None:
    from src.contracts.domains.decision import Decision
    from src.contracts.domains.quote import QuoteRaw
    from src.contracts.enums.trading import DecisionAction, HoldingPeriod

    runtime = app.dependency_overrides[RuntimeDependencies.from_request]()
    now = datetime.now(UTC)
    decision = Decision(
        decision_id="dec_alert_test",
        decision_at=now,
        action=DecisionAction.BUY,
        delta_g=1.0,
        target_inventory_g=1.0,
        horizon_primary=HoldingPeriod.W1,
        reason_codes=["TEST"],
    )
    quote = QuoteRaw(
        quote_id="q_alert",
        source_id="test_src",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=now,
        received_at=now,
        available_at=now,
        is_tradable=True,
    )
    created = runtime.alerts.create_alert(decision, quote, runtime.ledger.take_snapshot(now))
    assert created is not None

    listed = client.get("/api/v1/alerts")
    assert listed.status_code == 200
    assert [item["alert_id"] for item in listed.json()] == [created.alert_id]

    confirmed = client.post(f"/api/v1/alerts/{created.alert_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "USER_CONFIRMED"


def test_concurrent_quote_recording_preserves_integrity() -> None:
    from concurrent.futures import ThreadPoolExecutor

    base_time = datetime.now(UTC)

    def post_one(index: int) -> int:
        response = client.post(
            "/api/v1/quotes",
            json={
                "quote_id": f"conc_{index}",
                "source_id": f"src_{index % 3}",
                "bid_price": 600.0 + index * 0.01,
                "ask_price": 600.5 + index * 0.01,
                "quote_at": (base_time + timedelta(milliseconds=index)).isoformat(),
                "is_tradable": True,
            },
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(post_one, range(12)))
    assert statuses == [200] * 12

    import sqlite3

    from src.contracts.domains.quote import QuoteRaw

    runtime = app.dependency_overrides[RuntimeDependencies.from_request]()
    assert not list(runtime.settings.resolved_data_dir.glob("quotes_*.jsonl"))
    with sqlite3.connect(runtime.settings.database_path) as conn:
        rows = conn.execute("SELECT raw_json FROM quotes WHERE quote_id LIKE 'conc_%'").fetchall()
    assert len(rows) == 12
    saved = [QuoteRaw.model_validate_json(row[0]) for row in rows]
    assert all(q.record_hash == runtime.quotes.compute_record_hash(q) for q in saved)


def test_cross_site_write_protection() -> None:
    now_str = datetime.now(UTC).isoformat()
    payload = {
        "quote_id": "csrf_test_01",
        "source_id": "test_src",
        "bid_price": 610.0,
        "ask_price": 610.5,
        "quote_at": now_str,
        "is_tradable": True,
        "product_code": "GOLD_ACCUMULATION",
    }

    # 1. 未授权外部 Origin 被拒绝
    resp_untrusted = client.post("/api/v1/quotes", json=payload, headers={"Origin": "https://evil.com"})
    assert resp_untrusted.status_code == 403
    assert resp_untrusted.json()["detail"] == "拒绝跨站写入请求"

    # 2. null Origin（如沙箱 iframe/本地文件）被拒绝
    resp_null = client.post("/api/v1/quotes", json=payload, headers={"Origin": "null"})
    assert resp_null.status_code == 403
    assert resp_null.json()["detail"] == "拒绝跨站写入请求"

    # 3. 浏览器显式声明 Sec-Fetch-Site: cross-site 且无白名单来源被拒绝
    resp_cross_site = client.post("/api/v1/quotes", json=payload, headers={"Sec-Fetch-Site": "cross-site"})
    assert resp_cross_site.status_code == 403
    assert resp_cross_site.json()["detail"] == "拒绝跨站写入请求"

    # 4. 未授权 Referer 跨站提交被拒绝
    resp_referer = client.post(
        "/api/v1/quotes", json=payload, headers={"Referer": "https://evil-phishing.org/index.html"}
    )
    assert resp_referer.status_code == 403
    assert resp_referer.json()["detail"] == "拒绝跨站写入请求"

    # 5. 协议不一致（https vs http 直连）被拒绝
    resp_scheme_mismatch = client.post(
        "/api/v1/quotes", json=payload, headers={"Host": "testserver", "Origin": "https://testserver"}
    )
    assert resp_scheme_mismatch.status_code == 403
    assert resp_scheme_mismatch.json()["detail"] == "拒绝跨站写入请求"

    # 6. 合法同源写入请求放行
    resp_same_origin = client.post(
        "/api/v1/quotes", json=payload, headers={"Host": "testserver", "Origin": "http://testserver"}
    )
    assert resp_same_origin.status_code == 200

    # 6.5 多跳反向代理逗号分隔的 X-Forwarded-Proto / X-Forwarded-Host 合法同源写入放行
    resp_multihop = client.post(
        "/api/v1/quotes",
        json=payload,
        headers={
            "Host": "internal:8000",
            "X-Forwarded-Proto": "https, http",
            "X-Forwarded-Host": "api.example.com, internal:8000",
            "Origin": "https://api.example.com",
        },
    )
    assert resp_multihop.status_code == 200

    # 7. 安全读取请求（GET）不受跨站拦截约束
    resp_get = client.get(
        "/api/v1/quotes/latest",
        headers={"Origin": "https://evil.com", "Sec-Fetch-Site": "cross-site"},
    )
    assert resp_get.status_code == 200


def test_cross_site_write_allowed_origins_and_api_token(tmp_path: Path) -> None:
    from pydantic import SecretStr

    custom_runtime = RuntimeDependencies(
        Settings.model_construct(
            data_dir=str(tmp_path),
            timesfm_enabled=False,
            news_enabled=False,
            allowed_origins=("https://trusted.company.com", "http://localhost:5173"),
            api_auth_token=SecretStr("super-secret-token"),
        )
    )
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: custom_runtime

    now_str = datetime.now(UTC).isoformat()
    payload = {
        "quote_id": "trusted_origin_q",
        "source_id": "test_src",
        "bid_price": 612.0,
        "ask_price": 612.5,
        "quote_at": now_str,
        "is_tradable": True,
        "product_code": "GOLD_ACCUMULATION",
    }

    # 1. 跨站且非白名单：即使携带合法 token 亦坚决拒绝
    resp_blocked = client.post(
        "/api/v1/quotes",
        json=payload,
        headers={"Origin": "https://evil.com", "Authorization": "Bearer super-secret-token"},
    )
    assert resp_blocked.status_code == 403
    assert resp_blocked.json()["detail"] == "拒绝跨站写入请求"

    # 2. 白名单 Origin 但缺失 Token：报 401
    resp_no_token = client.post(
        "/api/v1/quotes",
        json=payload,
        headers={"Origin": "https://trusted.company.com"},
    )
    assert resp_no_token.status_code == 401
    assert resp_no_token.json()["detail"] == "缺少或无效的 API 访问令牌"

    # 3. 白名单 Origin + Bearer Token：成功放行
    resp_valid_bearer = client.post(
        "/api/v1/quotes",
        json=payload,
        headers={
            "Origin": "https://trusted.company.com",
            "Authorization": "Bearer super-secret-token",
        },
    )
    assert resp_valid_bearer.status_code == 200
    assert resp_valid_bearer.json()["quote_id"] == "trusted_origin_q"

    # 4. 白名单 Referer + X-API-Key Token：成功放行
    payload["quote_id"] = "trusted_referer_q"
    resp_valid_api_key = client.post(
        "/api/v1/quotes",
        json=payload,
        headers={
            "Referer": "http://localhost:5173/dashboard",
            "X-API-Key": "super-secret-token",
        },
    )
    assert resp_valid_api_key.status_code == 200
    assert resp_valid_api_key.json()["quote_id"] == "trusted_referer_q"
