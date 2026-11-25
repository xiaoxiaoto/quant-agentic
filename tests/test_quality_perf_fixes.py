"""质量与性能审查 10 项修复回归测试。"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from src.configs.settings import Settings
from src.contracts.domains.account import OrderEvent
from src.contracts.domains.decision import Decision
from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
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
from src.repositories.runtime_state import RuntimeStateRepository
from src.runtime.dependencies import RuntimeDependencies
from src.services.execution.shadow_ledger import ShadowLedger
from src.services.execution.shadow_runner import ShadowRunner
from src.services.prediction.feature_engine import FeatureEngine


def _runtime(tmp_path: Path, **overrides: object) -> RuntimeDependencies:
    return RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            timesfm_enabled=False,
            news_enabled=False,
            live_quotes_enabled=False,
            jd_proactive_auth_enabled=False,
            **overrides,  # type: ignore[arg-type]
        )
    )


def _quote(
    quote_id: str,
    ts: datetime,
    *,
    bid: float = 600.0,
    ask: float = 600.4,
    received_at: datetime | None = None,
    available_at: datetime | None = None,
) -> QuoteRaw:
    rec = received_at or ts
    return QuoteRaw(
        quote_id=quote_id,
        source_id="test_bank",
        product_code="GOLD_ACCUMULATION",
        bid_price=bid,
        ask_price=ask,
        quote_at=ts,
        received_at=rec,
        available_at=available_at or rec,
        is_tradable=True,
        quality_state=DataQualityState.NORMAL,
    )


def test_ledger_persist_failure_rolls_back_and_retry_persists(tmp_path: Path) -> None:
    """[P1-1] 成交落盘失败时回滚内存状态，同订单号重试成功并重启不丢单。"""
    db_path = tmp_path / "ledger.sqlite3"
    store = RuntimeStateRepository(db_path)
    ledger = ShadowLedger(initial_cash=10000.0, fee_rule=FeeRule(buy_fee_rate=0.0), state_store=store)

    with (
        patch.object(store, "upsert_order", side_effect=RuntimeError("simulated sqlite disk error")),
        pytest.raises(RuntimeError, match="simulated sqlite disk error"),
    ):
        ledger.record_buy(client_order_id="ord_retry_1", grams=1.0, price=600.0, fee=0.0)

    # 失败后内存与数据库均应保持 0 持仓、0 订单
    assert ledger.take_snapshot().gold_total_g == 0.0
    assert ledger.take_snapshot().cash_available == 10000.0
    assert ledger.get_order_count() == 0
    assert store.load_orders() == []

    # 同订单号重试应真正写入 SQLite，重启后持仓一致
    ledger.record_buy(client_order_id="ord_retry_1", grams=1.0, price=600.0, fee=0.0)
    assert ledger.take_snapshot().gold_total_g == 1.0
    assert len(store.load_orders()) == 1

    restarted = ShadowLedger(initial_cash=10000.0, fee_rule=FeeRule(buy_fee_rate=0.0), state_store=store)
    assert restarted.take_snapshot().gold_total_g == 1.0
    assert restarted.take_snapshot().cash_available == 9400.0


def test_paper_and_live_ledgers_are_isolated_and_persisted_separately(tmp_path: Path) -> None:
    """[P1-2] 本地模拟成交不影响实盘对账，京东实盘初始化不清空模拟盘流水。"""
    runtime = _runtime(tmp_path)
    try:
        now = datetime.now(UTC)
        q = _quote("iso_q_1", now)
        runtime.quotes.save_quote(q)

        _, fill = runtime.paper.submit_local_order(direction=TradeDirection.BUY, grams=2.0, quote=q, as_of=now)
        assert fill.is_filled is True
        assert runtime.paper_ledger.take_snapshot().gold_total_g == pytest.approx(2.0)
        assert runtime.ledger.take_snapshot().gold_total_g == pytest.approx(0.0)

        # 实盘对账（银行 0 持仓）必须一致且不触发熔断
        report, risk = runtime.live.reconcile_snapshot(bank_cash=100000.0, bank_gold_g=0.0)
        assert report.matched is True
        assert risk == RiskState.NORMAL

        # 实盘账本重置不会清空模拟盘订单
        runtime.ledger.reset_and_seed_holdings(initial_cash=50000.0, holdings=[("CMBC", 1.0, 600.0)])
        assert runtime.ledger.take_snapshot().gold_total_g == pytest.approx(1.0)
        assert runtime.paper_ledger.take_snapshot().gold_total_g == pytest.approx(2.0)
    finally:
        asyncio.run(runtime.close())

    # 重启后两个账本各自恢复准确状态
    restarted = _runtime(tmp_path)
    try:
        assert restarted.ledger.take_snapshot().gold_total_g == pytest.approx(1.0)
        assert restarted.paper_ledger.take_snapshot().gold_total_g == pytest.approx(2.0)
    finally:
        asyncio.run(restarted.close())


def test_jd_init_custom_cash_and_interest_survives_restart(tmp_path: Path) -> None:
    """[P1-3] 自定义初始资金与生息差额在京东初始化后，重启恢复完全一致。"""
    db_path = tmp_path / "jd_init.sqlite3"
    store = RuntimeStateRepository(db_path)
    ledger = ShadowLedger(initial_cash=100000.0, state_store=store)
    ts = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    jd_order = OrderEvent(
        client_order_id="jd_buy_001",
        bank_order_id="BANK_001",
        order_type="MARKET",
        direction=TradeDirection.BUY,
        requested_grams=1.0,
        filled_grams=1.0,
        execution_price=1000.0,
        fee=0.0,
        state=OrderState.FILLED,
        bank_event_at=ts,
        received_at=ts,
    )
    snap = ledger.reset_and_replay_orders(initial_cash=2900.0, orders=[jd_order], target_gold_g=1.1)
    assert snap.cash_available == pytest.approx(1900.0)
    assert snap.gold_total_g == pytest.approx(1.1)
    assert snap.interest_credited_g == pytest.approx(0.1)

    restarted = ShadowLedger(initial_cash=100000.0, state_store=store)
    restarted_snap = restarted.take_snapshot()
    assert restarted_snap.cash_available == pytest.approx(1900.0)
    assert restarted_snap.gold_total_g == pytest.approx(1.1)
    assert restarted_snap.interest_credited_g == pytest.approx(0.1)


def test_manual_paper_order_rejects_stale_quote_and_uses_submission_time(tmp_path: Path) -> None:
    """[P1-4] 手动模拟单拒绝数月前过期报价，新鲜报价以实际下单时间记账。"""
    runtime = _runtime(tmp_path)
    try:
        jan = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        nov = datetime(2026, 11, 25, 0, 0, tzinfo=UTC)
        old_quote = _quote("q_jan", jan)
        runtime.quotes.save_quote(old_quote)

        _, stale_fill = runtime.paper.submit_local_order(direction=TradeDirection.BUY, grams=1.0, as_of=nov)
        assert stale_fill.is_filled is False

        fresh_quote = _quote("q_nov", nov - timedelta(seconds=5))
        runtime.quotes.save_quote(fresh_quote)
        _, fresh_fill = runtime.paper.submit_local_order(direction=TradeDirection.BUY, grams=1.0, as_of=nov)
        assert fresh_fill.is_filled is True
        assert fresh_fill.filled_at == nov
    finally:
        asyncio.run(runtime.close())


def test_jsonl_backtest_excludes_quotes_received_after_end_time(tmp_path: Path) -> None:
    """[P1-5] 离线 JSONL 回测与 SQLite 一致，排除截止时间之后才收到的晚到报价。"""
    runtime = _runtime(tmp_path)
    try:
        end_time = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
        late_quote = _quote(
            "q_late",
            ts=end_time - timedelta(minutes=10),
            received_at=end_time + timedelta(minutes=5),
            available_at=end_time + timedelta(minutes=5),
        )
        runtime.quotes.save_quote(late_quote)
        jsonl_path = tmp_path / "late.jsonl"
        jsonl_path.write_text(json.dumps(late_quote.model_dump(mode="json")) + "\n", encoding="utf-8")

        repo_summary = runtime.backtest.run(end_time=end_time)
        file_summary = runtime.backtest.run(end_time=end_time, quotes_file=jsonl_path)
        assert repo_summary.quote_count == 0
        assert file_summary.quote_count == 0
    finally:
        asyncio.run(runtime.close())


def test_kill_switch_during_evaluate_once_blocks_alert_creation(tmp_path: Path) -> None:
    """[P1-6] 特征计算期间触发急停后，正在执行的评估不得生成或持久化提醒。"""
    runtime = _runtime(tmp_path)
    try:
        now = datetime.now(UTC)
        q = _quote("q_kill_race", now)
        runtime.quotes.save_quote(q)

        orig_extract = runtime.features.extract_features

        def _extract_and_halt(*args: object, **kwargs: object):
            runtime.live.trigger_kill_switch("MID_EVAL_HALT")
            return orig_extract(*args, **kwargs)  # type: ignore[arg-type]

        def _force_buy(**_kwargs: object) -> Decision:
            return Decision(
                decision_id="dec_race_buy",
                decision_at=now,
                action=DecisionAction.BUY,
                target_inventory_g=2.0,
                delta_g=2.0,
                horizon_primary=HoldingPeriod.W1,
            )

        runtime.features.extract_features = _extract_and_halt  # type: ignore[method-assign]
        runtime.decision.evaluate_decision = _force_buy  # type: ignore[method-assign]

        result = runtime.live.evaluate_once(quote=q, as_of=now)
        assert result.alert is None
        assert result.kill_switch_active is True
        assert "KILL_SWITCH_ACTIVE" in result.risk_violations
        assert runtime.alerts.list_alerts() == []
    finally:
        asyncio.run(runtime.close())


def test_mark_executed_keeps_alert_open_on_ledger_failure_and_allows_idempotent_retry(tmp_path: Path) -> None:
    """[P1-7] 人工成交登记因余额不足失败时提醒不卡在已执行，修正后可重试且支持幂等重试。"""
    runtime = _runtime(tmp_path, default_initial_cash=1000.0)
    try:
        now = datetime.now(UTC)
        q = _quote("q_exec_retry", now, bid=600.0, ask=600.0)
        dec = Decision(
            decision_id="dec_exec_retry",
            decision_at=now,
            action=DecisionAction.BUY,
            target_inventory_g=2.0,
            delta_g=2.0,
            horizon_primary=HoldingPeriod.W1,
        )
        alert = runtime.alerts.create_alert(dec, q, runtime.ledger.take_snapshot(now))
        assert alert is not None

        # 余额仅 1000，买入 2g @ 600 = 1200 应失败且提醒保持 ALERT_SENT
        with pytest.raises(ValueError, match="可用资金不足"):
            runtime.live.mark_executed(alert.alert_id, filled_grams=2.0, filled_price=600.0, fee=0.0)
        current = runtime.alerts.get_alert(alert.alert_id)
        assert current is not None
        assert current.state == AlertState.ALERT_SENT

        # 修正克重为 1g @ 600 重试成功
        executed = runtime.live.mark_executed(alert.alert_id, filled_grams=1.0, filled_price=600.0, fee=0.0)
        assert executed.state == AlertState.USER_EXECUTED
        assert runtime.ledger.take_snapshot().gold_total_g == pytest.approx(1.0)

        # 相同成交明细幂等重试应返回已执行提醒
        retried = runtime.live.mark_executed(alert.alert_id, filled_grams=1.0, filled_price=600.0, fee=0.0)
        assert retried.state == AlertState.USER_EXECUTED
        assert runtime.ledger.take_snapshot().gold_total_g == pytest.approx(1.0)
    finally:
        asyncio.run(runtime.close())


def test_incremental_shadow_runner_matches_legacy_extract_features() -> None:
    """[P1-8] 增量特征回测输出与全量重算完全一致。"""
    base = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    quotes = [
        _quote(
            f"perf_q_{i}",
            base + timedelta(hours=i),
            bid=600.0 + (i % 13) * 1.5 - (i % 7) * 0.8,
            ask=600.4 + (i % 13) * 1.5 - (i % 7) * 0.8,
        )
        for i in range(120)
    ]

    class _LegacyOnlyFeatureEngine:
        def __init__(self, inner: FeatureEngine) -> None:
            self._inner = inner

        def extract_features(self, *args: object, **kwargs: object):
            return self._inner.extract_features(*args, **kwargs)  # type: ignore[arg-type]

    inc_report = ShadowRunner(initial_cash=50000.0, feature_engine=FeatureEngine()).run_shadow_simulation(quotes)
    leg_report = ShadowRunner(
        initial_cash=50000.0,
        feature_engine=_LegacyOnlyFeatureEngine(FeatureEngine()),  # type: ignore[arg-type]
    ).run_shadow_simulation(quotes)

    assert inc_report.final_equity == leg_report.final_equity
    assert inc_report.total_return_pct == leg_report.total_return_pct
    assert inc_report.max_drawdown_pct == leg_report.max_drawdown_pct
    assert inc_report.total_trades == leg_report.total_trades
    assert inc_report.total_fees_cny == leg_report.total_fees_cny
    assert inc_report.sharpe_ratio == leg_report.sharpe_ratio


def test_latest_quote_query_avoids_temp_btree_sort(tmp_path: Path) -> None:
    """[P2-9] 最新报价查询使用覆盖时间倒序的索引，无需临时 B 树排序。"""
    repo = QuoteRepository(tmp_path)
    base = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    for i in range(10):
        repo.save_quote(_quote(f"idx_q_{i}", base + timedelta(hours=i)))

    conn = sqlite3.connect(tmp_path / "quant-agentic.sqlite3")
    try:
        plan_rows = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT raw_json FROM quotes
            WHERE product_code = ? AND quality_state = ? AND quote_at_utc <= ? AND pit_cutoff_utc <= ?
            ORDER BY quote_at_utc DESC, id DESC
            LIMIT 1
            """,
            ("GOLD_ACCUMULATION", "NORMAL", "2026-03-02T00:00:00.000000Z", "2026-03-02T00:00:00.000000Z"),
        ).fetchall()
    finally:
        conn.close()

    plan_text = " ".join(str(row) for row in plan_rows).upper()
    assert "USE TEMP B-TREE FOR ORDER BY" not in plan_text


def test_ledger_orders_route_supports_pagination(tmp_path: Path) -> None:
    """[P2-10] 订单流水接口支持 offset 与 limit 分页参数。"""
    runtime = _runtime(tmp_path)
    for i in range(5):
        runtime.ledger.record_buy(client_order_id=f"page_ord_{i}", grams=1.0, price=600.0, fee=0.0)

    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime
    try:
        with TestClient(app) as client:
            resp = client.get("/api/v1/ledger/orders?offset=1&limit=2")
            assert resp.status_code == 200
            data = resp.json()
            assert [item["client_order_id"] for item in data] == ["page_ord_1", "page_ord_2"]
    finally:
        app.dependency_overrides.clear()
        asyncio.run(runtime.close())
