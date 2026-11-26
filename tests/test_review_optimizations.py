"""代码审查优化项回归测试。"""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from src.configs.settings import Settings
from src.contracts.domains.account import AccountSnapshot, OrderEvent
from src.contracts.domains.decision import Decision
from src.contracts.domains.event import NewsEvent
from src.contracts.domains.jd_collection import JDNewsArticle
from src.contracts.domains.product import FeeRule
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
from src.repositories.jd_collection import JDCollectionRepository
from src.repositories.reference_quote import ReferenceQuoteRepository
from src.repositories.runtime_state import RuntimeStateRepository
from src.runtime.dependencies import RuntimeDependencies
from src.services.execution.shadow_ledger import ShadowLedger
from src.services.prediction.feature_engine import FeatureEngine
from src.services.prediction.training_pipeline import TrainingPipelineService
from src.services.risk.news_risk import NewsRiskEngine


def _runtime(tmp_path: Path, **overrides: object) -> RuntimeDependencies:
    return RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            timesfm_enabled=False,
            news_enabled=False,
            live_quotes_enabled=False,
            market_bars_enabled=False,
            news_sync_enabled=False,
            jd_holdings_sync_enabled=False,
            jd_proactive_auth_enabled=False,
            **overrides,  # type: ignore[arg-type]
        )
    )


def test_reconciliation_http_routes_trigger_halt_and_kill_switch(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        with TestClient(app) as client:
            snap_resp = client.post(
                "/api/v1/reconciliation/snapshot",
                json={"bank_cash": 90000.0, "bank_gold_g": 0.0},
            )
            assert snap_resp.status_code == 200
            assert snap_resp.json()["recommended_risk_state"] == RiskState.HALT_RECONCILE.value
            assert runtime.execution.is_kill_switch_active() is True
            assert runtime.live.get_status()["risk_state"] == RiskState.HALT_RECONCILE.value

            runtime.live.reset_kill_switch()
            assert runtime.execution.is_kill_switch_active() is False

            ord_resp = client.post(
                "/api/v1/reconciliation/orders",
                json={
                    "local_orders": [
                        {
                            "client_order_id": "cid_missing",
                            "direction": "BUY",
                            "requested_grams": 1.0,
                            "filled_grams": 1.0,
                            "execution_price": 600.0,
                            "state": "FILLED",
                        }
                    ],
                    "bank_statements": [],
                },
            )
            assert ord_resp.status_code == 200
            assert ord_resp.json()["recommended_risk_state"] == RiskState.HALT_RECONCILE.value
            assert runtime.execution.is_kill_switch_active() is True
    finally:
        app.dependency_overrides.clear()
        asyncio.run(runtime.close())


def test_alert_execute_rejects_partial_fill_and_guards_expiry_boundary(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    now = datetime.now(UTC)
    quote = QuoteRaw(
        quote_id="q_alt_pair",
        source_id="src",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=now,
        received_at=now,
        available_at=now,
        is_tradable=True,
    )
    decision = Decision(
        decision_id="dec_alt_pair",
        decision_at=now,
        action=DecisionAction.BUY,
        delta_g=1.0,
        target_inventory_g=1.0,
        horizon_primary=HoldingPeriod.W1,
        expires_at=now + timedelta(minutes=5),
    )
    alert = runtime.alerts.create_alert(decision, quote, AccountSnapshot(cash_available=50000.0, as_of=now))
    assert alert is not None

    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        with TestClient(app) as client:
            resp = client.post(f"/api/v1/alerts/{alert.alert_id}/execute", json={"filled_grams": 1.0})
            assert resp.status_code == 422

        # 精确命中过期边界时不得出现账本已记账但提醒失败的情况
        alert.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(ValueError, match="已过有效期"):
            runtime.live.mark_executed(alert.alert_id, filled_grams=1.0, filled_price=600.5, fee=0.0)
        assert runtime.ledger.get_order_count() == 0
        current = runtime.alerts.get_alert(alert.alert_id)
        assert current is not None
        assert current.state == AlertState.EXPIRED
    finally:
        app.dependency_overrides.clear()
        asyncio.run(runtime.close())


def test_runtime_state_rejects_cross_scope_order_id_collision(tmp_path: Path) -> None:
    store = RuntimeStateRepository(tmp_path / "orders.sqlite3")
    order = OrderEvent(
        client_order_id="shared_cid_1",
        direction=TradeDirection.BUY,
        requested_grams=1.0,
        filled_grams=1.0,
        execution_price=600.0,
        state=OrderState.FILLED,
        received_at=datetime.now(UTC),
    )
    store.upsert_order(order, scope="live")
    with pytest.raises(ValueError, match="其他账本范围"):
        store.upsert_order(order, scope="paper")


def test_reference_series_returns_full_window_beyond_500(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        repo = ReferenceQuoteRepository(tmp_path / "references", database_path=runtime.settings.database_path)
        base = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        for i in range(560):
            ts = base + timedelta(hours=i)
            repo.save(
                ReferenceQuote(
                    quote_id=f"ref_{i:04d}",
                    product_code="CMBC-JCJ",
                    price=600.0 + i * 0.1,
                    unit="CNY/g",
                    quote_at=ts,
                    received_at=ts,
                    available_at=ts,
                    payload_hash=f"{i + 1:064x}",
                )
            )
        series = runtime.live_quotes.query_series(
            "CMBC-JCJ",
            start_time=base,
            end_time=base + timedelta(hours=600),
            limit=None,
        )
        assert len(series) == 560

        anchor = QuoteRaw(
            quote_id="anchor_q",
            source_id="src",
            product_code="GOLD_ACCUMULATION",
            bid_price=655.0,
            ask_price=656.0,
            quote_at=base + timedelta(hours=560),
            received_at=base + timedelta(hours=560),
            available_at=base + timedelta(hours=560),
            is_tradable=True,
        )
        history = runtime.live._load_reference_assist_history(
            "CMBC-JCJ",
            anchor,
            product_code="GOLD_ACCUMULATION",
            as_of=base + timedelta(hours=560),
        )
        # 560 条历史 + 1 条当前报价
        assert len(history) == 561
    finally:
        asyncio.run(runtime.close())


def test_pending_articles_prioritize_newest_unattempted(tmp_path: Path) -> None:
    repo = JDCollectionRepository(tmp_path / "jd.sqlite3")
    base = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    articles = [
        JDNewsArticle(
            article_id=f"flash:jd_flash_{i}",
            event_id=f"jd_flash_{i}",
            channel="flash",
            title=f"快讯 {i}",
            content=f"内容 {i}",
            source="JD_FLASH",
            published_at=base + timedelta(minutes=i),
            collected_at=base + timedelta(minutes=i),
            raw={"id": i},
        )
        for i in range(5)
    ]
    repo.save_articles(articles)
    # 标记最新一条尝试失败，应排到未尝试记录之后
    repo.mark_attempt("flash:jd_flash_4", base + timedelta(minutes=10))
    pending = repo.pending_articles(5)
    assert [item.article_id for item in pending] == [
        "flash:jd_flash_3",
        "flash:jd_flash_2",
        "flash:jd_flash_1",
        "flash:jd_flash_0",
        "flash:jd_flash_4",
    ]


def test_training_pipeline_incremental_build_matches_legacy(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        base = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        for i in range(120):
            ts = base + timedelta(hours=i)
            mid = 600.0 + 10.0 * math.sin(i / 5.0)
            runtime.quotes.save_quote(
                QuoteRaw(
                    quote_id=f"inc_q_{i:04d}",
                    source_id="bank_hist",
                    product_code="GOLD_ACCUMULATION",
                    bid_price=round(mid - 0.2, 4),
                    ask_price=round(mid + 0.2, 4),
                    quote_at=ts,
                    received_at=ts,
                    available_at=ts,
                    is_tradable=True,
                    quality_state=DataQualityState.NORMAL,
                )
            )

        class _LegacyOnlyEngine:
            def __init__(self, inner: FeatureEngine) -> None:
                self._inner = inner

            def extract_features(self, *args: object, **kwargs: object):
                return self._inner.extract_features(*args, **kwargs)  # type: ignore[arg-type]

        fast_samples, _, _ = runtime.training_pipeline.build_samples(
            horizons=[HoldingPeriod.H1, HoldingPeriod.D1],
            exclude_synthetic=False,
        )
        legacy_pipeline = TrainingPipelineService(
            quotes=runtime.quotes,
            feature_engine=_LegacyOnlyEngine(FeatureEngine()),  # type: ignore[arg-type]
            fee_rule=FeeRule(buy_fee_rate=runtime.settings.buy_fee_rate, sell_fee_rate=runtime.settings.sell_fee_rate),
        )
        legacy_samples, _, _ = legacy_pipeline.build_samples(
            horizons=[HoldingPeriod.H1, HoldingPeriod.D1],
            exclude_synthetic=False,
        )
        assert [s.model_dump(mode="json") for s in fast_samples] == [s.model_dump(mode="json") for s in legacy_samples]
    finally:
        asyncio.run(runtime.close())


def test_news_risk_engine_dedup_and_window_index() -> None:
    engine = NewsRiskEngine(severity_threshold=0.7, confidence_threshold=0.6)
    base = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    old = NewsEvent(
        event_id="old_high",
        title="旧突发事件",
        content="旧高危正文",
        source="FEED",
        published_at=base - timedelta(hours=30),
        available_at=base - timedelta(hours=30),
        event_type="GEOPOLITICAL",
        severity=0.9,
        confidence=0.9,
        summary="旧摘要",
    )
    recent = NewsEvent(
        event_id="recent_high",
        title="新突发事件",
        content="新高危正文",
        source="FEED",
        published_at=base - timedelta(hours=2),
        available_at=base - timedelta(hours=2),
        event_type="MONETARY",
        severity=0.9,
        confidence=0.9,
        summary="新摘要",
    )
    engine.ingest_news(old)
    engine.ingest_news(recent)
    assert engine.has_duplicate_news(" 新突发事件 ", " 新高危正文 ") is True
    assert engine.has_duplicate_news("不存在", "内容") is False
    flags = engine.get_active_risk_flags(base)
    assert flags == ["NEWS_RISK_MONETARY_recent_high"]


def test_shadow_ledger_weighted_cost_replay_consistency() -> None:
    ledger = ShadowLedger(initial_cash=100000.0, fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.0))
    t0 = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    orders = [
        OrderEvent(
            client_order_id="jd_b1",
            direction=TradeDirection.BUY,
            requested_grams=10.0,
            filled_grams=10.0,
            execution_price=600.0,
            fee=0.0,
            state=OrderState.FILLED,
            bank_event_at=t0,
            received_at=t0,
        ),
        OrderEvent(
            client_order_id="jd_b2",
            direction=TradeDirection.BUY,
            requested_grams=10.0,
            filled_grams=10.0,
            execution_price=700.0,
            fee=0.0,
            state=OrderState.FILLED,
            bank_event_at=t0 + timedelta(hours=1),
            received_at=t0 + timedelta(hours=1),
        ),
        OrderEvent(
            client_order_id="jd_s1",
            direction=TradeDirection.SELL,
            requested_grams=10.0,
            filled_grams=10.0,
            execution_price=700.0,
            fee=0.0,
            state=OrderState.FILLED,
            bank_event_at=t0 + timedelta(hours=2),
            received_at=t0 + timedelta(hours=2),
        ),
    ]
    snap = ledger.reset_and_replay_orders(initial_cash=100000.0, orders=orders)
    # 移动加权成本 650/g，剩余 10g 在持成本 6500，因此可用现金应为 100000 - 6500 = 93500
    assert snap.gold_total_g == pytest.approx(10.0)
    assert snap.cost_basis_per_g == pytest.approx(650.0)
    assert snap.cash_available == pytest.approx(93500.0)


def test_http_routes_reject_path_traversal_outside_data_dir(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        with TestClient(app) as client:
            bt = client.post("/api/v1/backtest/run", json={"quotes_file": "../../etc/passwd"})
            assert bt.status_code == 400
            tr = client.post(
                "/api/v1/models/train",
                json={"export_samples_path": "/tmp/escape_samples.jsonl"},
            )
            assert tr.status_code == 400
    finally:
        app.dependency_overrides.clear()
        asyncio.run(runtime.close())
