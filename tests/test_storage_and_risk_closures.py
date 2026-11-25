"""SQLite 规范表存储、合成模拟报价桥接、风控闭环与京东自动对账回归测试。"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
from src.clients.jd_gold import JDGoldClient
from src.configs.settings import Settings
from src.contracts.domains.account import AccountSnapshot, OrderEvent
from src.contracts.domains.decision import Decision, RiskBudget
from src.contracts.domains.event import MacroCalendarEvent, NewsEvent
from src.contracts.domains.feature import ModelPrediction
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.reference_quote import ReferenceQuote
from src.contracts.enums.trading import (
    AlertState,
    DataQualityState,
    DecisionAction,
    HoldingPeriod,
    OrderState,
    RiskState,
    TradeDirection,
)
from src.repositories.quote import QuoteRepository
from src.repositories.reference_quote import ReferenceQuoteRepository
from src.repositories.runtime_state import RuntimeStateRepository
from src.runtime.dependencies import RuntimeDependencies
from src.services.decision.decision_engine import DecisionEngine
from src.services.risk.alert_service import AlertService
from src.services.risk.news_risk import NewsRiskEngine
from src.services.risk.risk_engine import RiskEngine


def _runtime(tmp_path: Path, **overrides: object) -> RuntimeDependencies:
    params: dict[str, object] = {
        "data_dir": str(tmp_path),
        "timesfm_enabled": False,
        "news_enabled": False,
        "live_quotes_enabled": True,
        "live_quotes_products": ("CMBC-JCJ",),
    }
    params.update(overrides)
    return RuntimeDependencies(Settings(**params))  # type: ignore[arg-type]


def test_sqlite_wal_tables_and_reference_series_query(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    now = datetime(2026, 11, 24, 8, 0, 0, tzinfo=UTC)

    ref_repo = ReferenceQuoteRepository(tmp_path / "references", database_path=runtime.settings.database_path)
    for idx in range(3):
        ref_repo.save(
            ReferenceQuote(
                quote_id=f"ref_{idx}",
                product_code="CMBC-JCJ",
                price=800.0 + idx,
                quote_at=now + timedelta(minutes=idx),
                received_at=now + timedelta(minutes=idx),
                available_at=now + timedelta(minutes=idx),
                payload_hash=f"{idx:064x}",
            )
        )

    series = ref_repo.query_series(
        "CMBC-JCJ",
        start_time=now,
        end_time=now + timedelta(minutes=2),
    )
    assert [item.quote_id for item in series] == ["ref_0", "ref_1", "ref_2"]

    with sqlite3.connect(runtime.settings.database_path) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(journal_mode).lower() == "wal"
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    expected_tables = {
        "quotes",
        "reference_quotes",
        "ledger_orders",
        "alerts",
        "batch_instructions",
        "control_state",
        "news_events",
        "macro_events",
        "reconciliation_reports",
    }
    assert expected_tables.issubset(tables)


def test_legacy_kv_migration_and_news_alert_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "quant-agentic.sqlite3"
    now = datetime.now(UTC)
    legacy_order = OrderEvent(
        client_order_id="legacy_1",
        order_type="MARKET",
        direction=TradeDirection.BUY,
        requested_grams=2.0,
        filled_grams=2.0,
        execution_price=600.0,
        fee=0.0,
        state=OrderState.FILLED,
        received_at=now,
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE runtime_state (key TEXT PRIMARY KEY, payload TEXT)")
        conn.execute(
            "INSERT INTO runtime_state (key, payload) VALUES (?, ?)",
            ("ledger_orders", json.dumps([legacy_order.model_dump(mode="json")])),
        )
        conn.execute(
            "INSERT INTO runtime_state (key, payload) VALUES (?, ?)",
            ("control_state", json.dumps({"risk_state": "NO_NEW_RISK", "kill_switch_active": True})),
        )
        conn.commit()

    store = RuntimeStateRepository(db_path)
    assert [o.client_order_id for o in store.load_orders()] == ["legacy_1"]
    assert store.load_control_state() == (RiskState.NO_NEW_RISK, True)

    # 新闻与宏观日程事件跨重启持久化
    news_engine = NewsRiskEngine(state_store=store)
    news_engine.register_macro_event(
        MacroCalendarEvent(event_id="cpi_1", event_name="US CPI", scheduled_at=now + timedelta(hours=1))
    )
    news_engine.ingest_news(
        NewsEvent(
            event_id="news_1",
            source="wire",
            title="突发风险",
            content="高危地缘事件正文",
            published_at=now,
            available_at=now,
            event_type="GEO",
            severity=0.9,
            confidence=0.9,
            summary="高危地缘事件",
        )
    )
    reloaded_news = NewsRiskEngine(state_store=RuntimeStateRepository(db_path))
    assert reloaded_news.is_event_locked(now + timedelta(minutes=30))[0] is True
    assert any("NEWS_RISK_GEO_news_1" in f for f in reloaded_news.get_active_risk_flags(now + timedelta(minutes=5)))

    # 提醒过期批量扫描必须持久化
    alert_service = AlertService(default_validity_minutes=5.0, state_store=store)
    decision = Decision(
        decision_id="dec_expire_1",
        decision_at=now,
        action=DecisionAction.BUY,
        delta_g=2.0,
        target_inventory_g=2.0,
        horizon_primary=HoldingPeriod.W1,
        expires_at=now + timedelta(minutes=5),
    )
    quote = QuoteRaw(
        quote_id="q_exp",
        source_id="bank",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=now,
        received_at=now,
        available_at=now,
        is_tradable=True,
    )
    created = alert_service.create_alert(decision, quote, AccountSnapshot(cash_available=50000.0, as_of=now))
    assert created is not None
    expired_ids = alert_service.check_and_expire_alerts(current_time=now + timedelta(minutes=10))
    assert expired_ids == [created.alert_id]
    reloaded_alert = AlertService(state_store=RuntimeStateRepository(db_path)).get_alert(created.alert_id)
    assert reloaded_alert is not None
    assert reloaded_alert.state == AlertState.EXPIRED


def test_synthetic_paper_quote_bridge_isolated_from_live(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        synthetic_paper_quote_enabled=True,
        synthetic_paper_reference_product="CMBC-JCJ",
        synthetic_paper_spread_pct=0.002,
    )
    now = datetime.now(UTC)
    envelope = {
        "success": True,
        "resultCode": 0,
        "resultData": {
            "status": "SUCCESS",
            "datas": {
                "id": 90001,
                "productSku": "21001001000001",
                "demode": False,
                "price": "800.00",
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

    # 即使存在更早的历史非合成报价，开启桥接后模拟盘也应优先使用更新的实时参考价
    stale_real = QuoteRaw(
        quote_id="old_real_quote",
        source_id="old_bank",
        product_code="GOLD_ACCUMULATION",
        bid_price=600.0,
        ask_price=601.0,
        quote_at=now - timedelta(days=2),
        received_at=now - timedelta(days=2),
        available_at=now - timedelta(days=2),
        is_tradable=True,
        is_synthetic=False,
    )
    runtime.quotes.save_quote(stale_real)

    # 模拟盘可以读取到更新的合成报价并完成评估
    paper_res = runtime.paper.step_local(product_code="GOLD_ACCUMULATION", as_of=now + timedelta(seconds=1))
    assert paper_res.quote_id is not None
    assert paper_res.quote_id.startswith("syn_CMBC-JCJ_")

    # 实盘必须严格排除合成报价（只看到旧的非合成报价，不碰合成报价）
    live_res = runtime.live.evaluate_once(product_code="GOLD_ACCUMULATION", as_of=now + timedelta(seconds=1))
    assert live_res.quote_id == "old_real_quote"

    synthetic_quote = QuoteRepository(tmp_path, database_path=runtime.settings.database_path).get_latest_as_of(
        now + timedelta(seconds=1),
        exclude_synthetic=False,
    )
    assert synthetic_quote is not None
    assert synthetic_quote.is_synthetic is True
    with pytest.raises(ValueError, match="合成"):
        runtime.live.evaluate_once(quote=synthetic_quote, as_of=now + timedelta(seconds=1))


def test_daily_weekly_loss_circuit_breakers_and_cooldown() -> None:
    now = datetime(2026, 11, 24, 10, 0, 0, tzinfo=UTC)
    account = AccountSnapshot(cash_available=100000.0, gold_total_g=5.0, gold_available_g=5.0, as_of=now)
    buy_dec = Decision(
        decision_id="dec_buy",
        decision_at=now,
        action=DecisionAction.BUY,
        delta_g=2.0,
        target_inventory_g=7.0,
        horizon_primary=HoldingPeriod.W1,
    )
    sell_dec = Decision(
        decision_id="dec_sell",
        decision_at=now,
        action=DecisionAction.SELL,
        delta_g=-2.0,
        target_inventory_g=3.0,
        horizon_primary=HoldingPeriod.W1,
    )
    risk = RiskEngine(risk_budget=RiskBudget(daily_loss_trigger_pct=0.01, weekly_loss_trigger_pct=0.03))

    # 日亏损超 1%：升级 NO_NEW_RISK 并拦截买入
    approved_buy, state_day, violations_day = risk.check_decision(
        buy_dec, account, 600.5, 600.0, daily_pnl=-1500.0, weekly_pnl=-1500.0
    )
    assert approved_buy.action == DecisionAction.ABSTAIN
    assert state_day == RiskState.NO_NEW_RISK
    assert "DAILY_LOSS_LIMIT_BREACHED" in violations_day

    # 周亏损超 3%：升级 REDUCE_ONLY，拦截买入但放行减仓卖出
    approved_buy_w, state_week, violations_week = risk.check_decision(
        buy_dec, account, 600.5, 600.0, daily_pnl=-500.0, weekly_pnl=-4000.0
    )
    assert approved_buy_w.action == DecisionAction.ABSTAIN
    assert state_week == RiskState.REDUCE_ONLY
    assert "WEEKLY_LOSS_LIMIT_BREACHED" in violations_week

    approved_sell, state_sell, violations_sell = risk.check_decision(
        sell_dec, account, 600.5, 600.0, daily_pnl=-500.0, weekly_pnl=-4000.0
    )
    assert approved_sell.action == DecisionAction.SELL
    assert state_sell == RiskState.REDUCE_ONLY
    assert violations_sell == []

    # 冷却期拦截重复交易信号
    engine = DecisionEngine(edge_threshold=0.001, cooldown_seconds=300.0)
    quote = QuoteRaw(
        quote_id="q_cd",
        source_id="bank",
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
            expected_net_return=0.02,
            net_return_lcb=0.01,
            win_probability=0.75,
            uncertainty=0.005,
        )
    }
    cooled = engine.evaluate_decision(
        quote=quote,
        predictions=preds,
        account=account,
        target_buy_grams=2.0,
        as_of=now,
        last_signal_at=now - timedelta(seconds=60),
    )
    assert cooled.action == DecisionAction.HOLD
    assert "SIGNAL_COOLDOWN_ACTIVE" in cooled.reason_codes
    assert "COOLDOWN_ACTIVE" in cooled.risk_flags


def test_reconcile_with_jd_holdings_and_reports_route(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, live_quotes_enabled=False)
    runtime.ledger.record_buy(client_order_id="ord_1", grams=5.0, price=600.0, fee=0.0)

    # 模拟京东返回持仓克重与本地不一致（6.5g vs 5.0g）
    async def mock_holdings() -> dict[str, object]:
        return {
            "resultData": {
                "datas": [
                    {"bank": "CMBC", "holdGram": "4.0", "avgPrice": "600.0"},
                    {"bank": "CZB", "holdGram": "2.5", "avgPrice": "600.0"},
                ]
            }
        }

    runtime.jd_gold.trade.get_holdings = mock_holdings  # type: ignore[method-assign]

    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        with TestClient(app) as client:
            resp = client.post("/api/v1/reconciliation/jd-holdings")
            assert resp.status_code == 200
            data = resp.json()
            assert data["report"]["matched"] is False
            assert data["recommended_risk_state"] == RiskState.HALT_RECONCILE.value
            assert runtime.execution.is_kill_switch_active() is True

            reports_resp = client.get("/api/v1/reconciliation/reports")
            assert reports_resp.status_code == 200
            reports = reports_resp.json()
            assert len(reports) == 1
            assert reports[0]["report_id"] == data["report"]["report_id"]
    finally:
        app.dependency_overrides.clear()

    # 重启后应能从 SQLite 恢复对账报告与熔断状态
    restarted = _runtime(tmp_path, live_quotes_enabled=False)
    status = restarted.live.get_status()
    assert status["risk_state"] == RiskState.HALT_RECONCILE.value
    assert status["kill_switch_active"] is True
    assert status["last_reconciliation"] is not None


def test_init_ledger_from_jd_route(tmp_path: Path) -> None:
    runtime = RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            timesfm_enabled=False,
            news_enabled=False,
            live_quotes_enabled=False,
            default_initial_cash=200000.0,
        )
    )
    assert runtime.ledger.take_snapshot().cash_available == 200000.0
    runtime.ledger.record_buy(client_order_id="old_ord", grams=1.0, price=500.0, fee=0.0)

    async def mock_holdings() -> dict[str, object]:
        return {
            "resultData": {
                "datas": [
                    {"bank": "CMBC", "holdGram": "10.0", "avgPrice": "600.0"},
                    {"bank": "CZB", "holdGram": "5.0", "avgPrice": "620.0"},
                ]
            }
        }

    runtime.jd_gold.trade.get_holdings = mock_holdings  # type: ignore[method-assign]
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        with TestClient(app) as client:
            status_resp = client.get("/api/v1/live/status")
            assert status_resp.status_code == 200
            assert status_resp.json()["default_initial_cash"] == 200000.0

            resp = client.post("/api/v1/ledger/init-from-jd")
            assert resp.status_code == 200
            data = resp.json()
            assert data["gold_total_g"] == 15.0
            assert data["cash_available"] == 190900.0
            assert data["cost_basis_per_g"] is not None
            assert round(data["cost_basis_per_g"], 2) == round(9100.0 / 15.0, 2)

            orders_resp = client.get("/api/v1/ledger/orders")
            assert orders_resp.status_code == 200
            orders = orders_resp.json()
            assert len(orders) == 2
            assert all("jd_init_" in o["client_order_id"] for o in orders)
    finally:
        app.dependency_overrides.clear()


def test_init_ledger_replays_jd_gold_order_flow(tmp_path: Path) -> None:
    runtime = RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            timesfm_enabled=False,
            news_enabled=False,
            live_quotes_enabled=False,
            default_initial_cash=100000.0,
        )
    )

    async def mock_holdings() -> dict[str, object]:
        return {
            "totalGramAll": 6.001,
            "avgCostPrice": 600.0,
            "holdingList": [{"bankCode": "CZB", "bankName": "浙商银行", "totalGram": 6.001, "avgCostPrice": 600.0}],
        }

    async def mock_list_orders(
        _start_date: str,
        _end_date: str,
        *,
        page_no: int = 1,
        page_size: int = 200,
    ) -> dict[str, object]:
        assert page_no == 1
        assert page_size > 0
        return {
            "pageNo": 1,
            "pageSize": 200,
            "tradeOrderVoList": [
                # 应被过滤的非黄金订单与退款失败单
                {
                    "businessCode": "XJK",
                    "tradeTypeCode": "TRANSFER_IN",
                    "statusCode": "COMPLETE",
                    "orderId": "xjk_1",
                    "allAmount": "50.00",
                    "bizTime": 1764000000000,
                },
                {
                    "businessCode": "GOLD",
                    "tradeTypeCode": "BUY_GOLD",
                    "statusCode": "REFUND_SUCC",
                    "orderId": "refund_1",
                    "allAmount": "600.00",
                    "extInfo": '{"og":"1.0000","ogp":"600.00"}',
                    "bizTime": 1764000001000,
                },
                # 真实买入 10g @ 600
                {
                    "businessCode": "GOLD",
                    "tradeTypeCode": "BUY_GOLD",
                    "subTradeTypeCode": "GOLD_CONDITIONAL_BUY",
                    "statusCode": "COMPLETE",
                    "orderId": "buy_100",
                    "bizOrderId": "CZBB100",
                    "allAmount": "6000.00",
                    "extInfo": '{"og":"10.0000","ogp":"600.00"}',
                    "bizTime": 1764000010000,
                },
                # 真实卖出 4g @ 650（含手续费 10.40 -> 到账 2589.60）
                {
                    "businessCode": "GOLD",
                    "tradeTypeCode": "SELL_GOLD",
                    "subTradeTypeCode": "GOLD_PAY_SELL",
                    "statusCode": "REDEEM_SUCC",
                    "orderId": "sell_200",
                    "bizOrderId": "CZBR200",
                    "allAmount": "2589.60",
                    "extInfo": '{"og":"4.0000","ogp":"650.00"}',
                    "bizTime": 1764000020000,
                },
            ],
        }

    runtime.jd_gold.trade.get_holdings = mock_holdings  # type: ignore[method-assign]
    runtime.jd_gold.trade.list_orders = mock_list_orders  # type: ignore[method-assign]
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        with TestClient(app) as client:
            resp = client.post("/api/v1/ledger/init-from-jd")
            assert resp.status_code == 200
            snap = resp.json()
            # 10g - 4g = 6g，加上生息差额 0.001g = 6.001g
            assert snap["gold_total_g"] == 6.001
            assert snap["interest_credited_g"] == 0.001
            # 可用现金 = 初始资金 100000 - 在持 6g 成本 3600 = 96400
            assert snap["cash_available"] == 96400.0
            assert snap["realized_pnl"] == 189.6

            orders = client.get("/api/v1/ledger/orders").json()
            assert [o["client_order_id"] for o in orders] == ["jd_buy_100", "jd_sell_200"]
            assert [o["bank_order_id"] for o in orders] == ["CZBB100", "CZBR200"]
            assert orders[1]["fee"] == 10.4
    finally:
        app.dependency_overrides.clear()

    # 重启后应保持相同的可用现金（初始资金 - 在持总成本）
    restarted = RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            timesfm_enabled=False,
            news_enabled=False,
            live_quotes_enabled=False,
            default_initial_cash=100000.0,
        )
    )
    assert restarted.ledger.take_snapshot().cash_available == 96400.0
