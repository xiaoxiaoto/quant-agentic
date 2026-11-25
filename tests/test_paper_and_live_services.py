"""双引擎模拟交易与实盘辅助闭环编排服务单元测试。"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.configs.settings import Settings
from src.contracts.domains.decision import Decision
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import (
    AlertState,
    DataQualityState,
    DecisionAction,
    HoldingPeriod,
    RiskState,
    TradeDirection,
)
from src.runtime.dependencies import RuntimeDependencies


def _build_runtime(tmp_path: Path) -> RuntimeDependencies:
    settings = Settings(
        data_dir=str(tmp_path),
        timesfm_enabled=False,
        news_enabled=False,
        live_quotes_enabled=False,
        jd_proactive_auth_enabled=False,
    )
    return RuntimeDependencies(settings)


def _seed_uptrend_quotes(runtime: RuntimeDependencies, count: int = 20) -> QuoteRaw:
    base = datetime.now(UTC) - timedelta(hours=count)
    last_quote: QuoteRaw | None = None
    for idx in range(count):
        ts = base + timedelta(hours=idx)
        bid = round(580.0 + idx * 2.0, 4)
        ask = round(bid + 0.2, 4)
        quote = QuoteRaw(
            quote_id=f"seed_q_{idx}",
            source_id="test_bank",
            bid_price=bid,
            ask_price=ask,
            quote_at=ts,
            received_at=ts,
            available_at=ts,
            is_tradable=True,
            quality_state=DataQualityState.NORMAL,
            product_code="GOLD_ACCUMULATION",
        )
        runtime.quotes.save_quote(quote)
        last_quote = quote
    assert last_quote is not None
    return last_quote


def test_paper_local_step_manual_order_and_kill_switch(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path)
    try:
        latest = _seed_uptrend_quotes(runtime, count=10)
        step_res = runtime.paper.step_local(product_code="GOLD_ACCUMULATION", quote=latest, as_of=latest.quote_at)
        assert step_res.quote_id == latest.quote_id
        assert step_res.approved_decision is not None

        order, fill = runtime.paper.submit_local_order(
            direction=TradeDirection.BUY,
            grams=2.0,
            product_code="GOLD_ACCUMULATION",
            quote=latest,
            as_of=latest.quote_at,
        )
        assert fill.is_filled is True
        assert order.filled_grams == 2.0
        assert runtime.paper_ledger.take_snapshot().gold_total_g == pytest.approx(2.0)
        assert runtime.ledger.take_snapshot().gold_total_g == pytest.approx(0.0)

        runtime.execution.trigger_kill_switch("TEST_HALT")
        blocked = runtime.paper.step_local(product_code="GOLD_ACCUMULATION", quote=latest, as_of=latest.quote_at)
        assert "KILL_SWITCH_ACTIVE" in blocked.risk_violations
    finally:
        asyncio.run(runtime.close())


def test_paper_jd_requires_confirm_and_forwards_trade(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path)
    try:
        runtime.jd_gold.trade.execute_sim_buy = AsyncMock(return_value={"status": "OK"})  # type: ignore[method-assign]
        with pytest.raises(ValueError, match="显式确认"):
            asyncio.run(
                runtime.paper.execute_jd_order(
                    direction=TradeDirection.BUY,
                    grams=1.0,
                    confirm=False,
                )
            )

        res = asyncio.run(
            runtime.paper.execute_jd_order(
                direction=TradeDirection.BUY,
                grams=1.0,
                confirm=True,
            )
        )
        assert res == {"status": "OK"}
        runtime.jd_gold.trade.execute_sim_buy.assert_awaited_once()
    finally:
        asyncio.run(runtime.close())


def test_live_alert_lifecycle_and_reconcile_halt(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path)
    try:
        latest = _seed_uptrend_quotes(runtime, count=5)

        def _force_buy(**_kwargs: object) -> Decision:
            return Decision(
                decision_id="dec_live_buy_01",
                decision_at=latest.quote_at,
                action=DecisionAction.BUY,
                target_inventory_g=2.0,
                delta_g=2.0,
                horizon_primary=HoldingPeriod.W1,
                reason_codes=["UNIT_TEST_SIGNAL"],
            )

        runtime.decision.evaluate_decision = _force_buy  # type: ignore[method-assign]
        step = runtime.live.evaluate_once(
            product_code="GOLD_ACCUMULATION",
            trade_grams=2.0,
            quote=latest,
            as_of=latest.quote_at,
        )
        assert step.alert is not None
        alert_id = step.alert.alert_id

        confirmed = runtime.live.confirm_alert(alert_id)
        assert confirmed.state == AlertState.USER_CONFIRMED

        executed = runtime.live.mark_executed(
            alert_id,
            filled_grams=2.0,
            filled_price=latest.ask_price,
            fee=0.0,
            bank_order_id="BANK_001",
        )
        assert executed.state == AlertState.USER_EXECUTED
        assert runtime.ledger.take_snapshot().gold_total_g == pytest.approx(2.0)

        # 刻意传入不一致的银行持仓，触发对账差异与自动熔断
        report, risk = runtime.live.reconcile_snapshot(bank_cash=100000.0, bank_gold_g=0.0)
        assert report.matched is False
        assert risk == RiskState.HALT_RECONCILE
        assert runtime.execution.is_kill_switch_active() is True

        blocked = runtime.live.evaluate_once(product_code="GOLD_ACCUMULATION", quote=latest, as_of=latest.quote_at)
        assert blocked.alert is None
        assert "HALT_RECONCILE_ACTIVE" in blocked.risk_violations
        assert "KILL_SWITCH_ACTIVE" in blocked.risk_violations

        runtime.live.reset_kill_switch()
        status = runtime.live.get_status()
        assert status["risk_state"] == RiskState.NORMAL.value
        assert status["kill_switch_active"] is False
    finally:
        asyncio.run(runtime.close())
