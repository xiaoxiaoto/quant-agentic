"""深度代码审查修复项专项回归测试。"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from main import app
from src.configs.settings import Settings
from src.contracts.domains.account import AccountSnapshot, OrderEvent
from src.contracts.domains.feature import ModelPrediction
from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.reference_quote import ReferenceQuote
from src.contracts.enums.trading import DataQualityState, DecisionAction, HoldingPeriod, OrderState, TradeDirection
from src.repositories.jd_collection import JDCollectionRepository
from src.repositories.runtime_state import RuntimeStateRepository
from src.runtime.dependencies import RuntimeDependencies
from src.services.decision.decision_engine import DecisionEngine
from src.services.execution.execution_adapter import ExecutionAdapter
from src.services.execution.paper_trading import PaperTradingService
from src.services.execution.shadow_ledger import ShadowLedger
from src.services.market.session_calendar import SessionCalendar


def _make_runtime(tmp_path: Path, **overrides: Any) -> RuntimeDependencies:
    base: dict[str, Any] = {
        "data_dir": str(tmp_path),
        "timesfm_enabled": False,
        "news_enabled": False,
        "live_quotes_enabled": False,
        "market_bars_enabled": False,
        "news_sync_enabled": False,
        "jd_holdings_sync_enabled": False,
        "jd_proactive_auth_enabled": False,
    }
    base.update(overrides)
    return RuntimeDependencies(Settings.model_construct(**base))


def test_export_samples_path_blocks_sqlite_and_existing_file_overwrite(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    try:
        with pytest.raises(ValueError, match="jsonl"):
            runtime.resolve_export_path("quant-agentic.sqlite3")

        existing = tmp_path / "existing.jsonl"
        existing.write_text("old\n", encoding="utf-8")
        with pytest.raises(ValueError, match="已存在"):
            runtime.resolve_export_path("existing.jsonl")

        valid = runtime.resolve_export_path("exports/samples.jsonl")
        assert valid == (tmp_path / "exports/samples.jsonl").resolve()
    finally:
        asyncio.run(runtime.close())


def test_api_auth_token_and_csrf_origin_protection(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, api_auth_token=SecretStr("secret-token-123"))
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        client = TestClient(app)
        assert client.get("/api/v1/live/status").status_code == 401
        assert client.get("/api/v1/live/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert (
            client.get("/api/v1/live/status", headers={"Authorization": "Bearer secret-token-123"}).status_code == 200
        )
        assert client.get("/api/v1/live/status", headers={"X-API-Key": "secret-token-123"}).status_code == 200

        # 跨站 Origin 发起 POST 应被拒绝
        csrf_resp = client.post(
            "/api/v1/live/resume",
            headers={
                "Authorization": "Bearer secret-token-123",
                "Origin": "https://evil.example",
                "Host": "127.0.0.1:8000",
            },
        )
        assert csrf_resp.status_code == 403
    finally:
        app.dependency_overrides.clear()
        asyncio.run(runtime.close())


def test_shadow_ledger_interest_gold_dilutes_cost_basis() -> None:
    ledger = ShadowLedger(initial_cash=100000.0, fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.0))
    now = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    buy_order = OrderEvent(
        client_order_id="jd_1",
        order_type="MARKET",
        direction=TradeDirection.BUY,
        requested_grams=10.0,
        filled_grams=10.0,
        execution_price=600.0,
        fee=0.0,
        state=OrderState.FILLED,
        bank_event_at=now,
        received_at=now,
    )
    snap = ledger.reset_and_replay_orders(initial_cash=100000.0, orders=[buy_order], target_gold_g=12.0)
    assert snap.gold_total_g == pytest.approx(12.0)
    assert snap.interest_credited_g == pytest.approx(2.0)
    # 10g * 600 / 12g = 500 元/克
    assert snap.cost_basis_per_g == pytest.approx(500.0)
    # 以 600 元/克重估：总市值 7200 - 持仓总成本 6000 = +1200 元
    assert float(ledger.mark_to_market(600.0)) == pytest.approx(1200.0)


def test_paper_trading_blocks_future_synthetic_quote_backdating() -> None:
    future_time = datetime(2026, 6, 2, 10, 0, tzinfo=UTC)
    past_as_of = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    ref = ReferenceQuote(
        quote_id="ref_future",
        source_id="JD_CMBC",
        product_code="CMBC-JCJ",
        price=610.0,
        unit="CNY/g",
        quote_at=future_time,
        received_at=future_time,
        available_at=future_time,
        payload_hash="test_hash",
        quality_state=DataQualityState.NORMAL,
    )
    with pytest.raises(ValueError, match="禁止构造前视合成报价"):
        PaperTradingService.build_synthetic_quote(ref, as_of=past_as_of)


def test_execution_adapter_batch_orders_respect_external_kill_switch() -> None:
    external_halted = True
    adapter = ExecutionAdapter(kill_switch_checker=lambda: external_halted)
    with pytest.raises(RuntimeError, match="Kill-Switch"):
        adapter.register_batch_instruction(
            instruction_id="b1",
            direction=TradeDirection.BUY,
            grams=2.0,
            limit_price=600.0,
            target_open=datetime.now(UTC) + timedelta(hours=1),
        )


def test_live_trading_order_envelope_pagination_and_string_biz_time(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    try:
        page1 = {
            "resultData": {
                "data": {
                    "tradeOrderVoList": [
                        {
                            "orderId": "o1",
                            "businessCode": "GOLD",
                            "tradeTypeCode": "BUY_GOLD",
                            "statusCode": "COMPLETE",
                            "gram": 1.0,
                            "price": 600.0,
                            "bizTime": "2026-05-01 09:30:00",
                        },
                        {
                            "orderId": "o2",
                            "businessCode": "GOLD",
                            "tradeTypeCode": "BUY_GOLD",
                            "statusCode": "COMPLETE",
                            "gram": 1.0,
                            "price": 605.0,
                            "bizTime": "1777600000000",
                        },
                    ]
                }
            }
        }
        page2 = {
            "resultData": {
                "data": {
                    "tradeOrderVoList": [
                        {
                            "orderId": "o3",
                            "businessCode": "GOLD",
                            "tradeTypeCode": "BUY_GOLD",
                            "statusCode": "COMPLETE",
                            "gram": 1.0,
                            "price": 610.0,
                            "bizTime": "2026-05-03 10:00:00",
                        }
                    ]
                }
            }
        }
        mock_list = AsyncMock(side_effect=[page1, page2])
        runtime.jd_gold.trade.list_orders = mock_list  # type: ignore[method-assign]
        orders = asyncio.run(runtime.live._fetch_all_jd_gold_orders(page_size=2))
        assert len(orders) == 3
        assert mock_list.await_count == 2
        by_id = {o.client_order_id: o for o in orders}
        assert by_id["jd_o1"].bank_event_at == datetime(2026, 5, 1, 1, 30, tzinfo=UTC)

        # 过期令牌不应被视为 jd_logged_in=True
        expired_auth = MagicMock()
        expired_auth.access_token = SecretStr("expired_tok")
        expired_auth.actual_expires_at = datetime.now(UTC).timestamp() - 60
        runtime.jd_gold.auth_state = lambda: expired_auth  # type: ignore[method-assign]
        assert runtime.live.get_status()["jd_logged_in"] is False
    finally:
        asyncio.run(runtime.close())


def test_session_calendar_midweek_holiday_advances_to_next_day() -> None:
    tue = date(2026, 5, 5)  # 周二
    cal = SessionCalendar(holidays=[tue])
    tue_noon = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)
    next_open = cal.next_session_start(tue_noon)
    assert next_open.date() == date(2026, 5, 6)  # 周三而非下周一


def test_decision_cooldown_allows_risk_reducing_sell() -> None:
    engine = DecisionEngine(
        fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.004),
        cooldown_seconds=3600.0,
    )
    now = datetime(2026, 5, 10, 10, 0, tzinfo=UTC)
    quote = QuoteRaw(
        quote_id="q1",
        source_id="bank",
        product_code="GOLD",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=now,
        received_at=now,
        available_at=now,
        is_tradable=True,
        quality_state=DataQualityState.NORMAL,
    )
    preds = {
        HoldingPeriod.W1: ModelPrediction(
            horizon=HoldingPeriod.W1,
            expected_net_return=-0.02,
            net_return_lcb=-0.03,
            win_probability=0.2,
            uncertainty=0.01,
        )
    }
    account = AccountSnapshot(
        cash_available=50000.0,
        cash_frozen=0.0,
        gold_total_g=5.0,
        gold_available_g=5.0,
        gold_frozen_g=0.0,
        cost_basis_per_g=600.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        as_of=now,
    )
    decision = engine.evaluate_decision(
        quote=quote,
        predictions=preds,
        account=account,
        as_of=now,
        last_signal_at=now - timedelta(minutes=5),
    )
    assert decision.action == DecisionAction.SELL


def test_sqlite_concurrent_writes_under_immediate_transactions(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent.sqlite3"
    jd_repo = JDCollectionRepository(db_path)
    state_repo = RuntimeStateRepository(db_path)

    def worker(idx: int) -> None:
        now = datetime.now(UTC) + timedelta(microseconds=idx)
        jd_repo.save_snapshot(f"scope_{idx % 3}", {"v": idx}, now)
        state_repo.save_kill_switch_state(idx % 2 == 0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(40)))
    assert jd_repo.latest_snapshot("scope_0") is not None
