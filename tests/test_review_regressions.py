"""代码深度审查修复项回归测试。"""

import asyncio
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from main import app
from src.agents.timesfm import TimesFMAgent
from src.clients.jd_gold import JDGoldClient, JDGoldError
from src.configs.settings import Settings
from src.contracts.domains.account import AccountSnapshot, BankStatementItem, OrderEvent
from src.contracts.domains.decision import Decision
from src.contracts.domains.execution import RFQRequest
from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.reference_quote import ReferenceProductCatalog, ReferenceQuote
from src.contracts.enums.jd_gold import Bank
from src.contracts.enums.trading import DataQualityState, DecisionAction, HoldingPeriod, OrderState, TradeDirection
from src.contracts.schemas import QuoteIngestRequest, SnapshotReconcileRequest
from src.contracts.schemas.decision import DecisionQuote
from src.repositories.jd_gold_auth import JDGoldAuthRepository
from src.repositories.reference_quote import ReferenceQuoteRepository
from src.services.decision.baseline import BaselineEngine
from src.services.decision.cost_calculator import CostCalculator
from src.services.decision.decision_engine import DecisionEngine
from src.services.execution.execution_adapter import ExecutionAdapter
from src.services.execution.reconciliation import ReconciliationService
from src.services.execution.rfq_execution import RFQExecutionEngine
from src.services.execution.shadow_runner import ShadowRunner
from src.services.market.data_quality import DataQualityGuard
from src.services.market.live_quotes import LiveQuoteCollector
from src.services.market.session_calendar import SessionCalendar


def test_jd_auth_repo_sanitizes_corrupt_payload_without_leaking_secret(tmp_path: Path) -> None:
    """损坏的授权载荷报错不得泄漏明文令牌。"""
    db_path = tmp_path / "auth.sqlite3"
    repo = JDGoldAuthRepository(db_path, scope="https://bff.test")
    secret_marker = "SUPER_SECRET_TOKEN_XYZ"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO jd_gold_auth (scope, payload) VALUES (?, ?)",
            ("https://bff.test", f'{{"access_token": "{secret_marker}", "broken": '),
        )
        conn.commit()
    with pytest.raises(ValueError) as exc_info:
        repo.load()
    assert secret_marker not in str(exc_info.value)


def test_jd_client_rejects_untrusted_authorize_url_host() -> None:
    """授权跳转链接拒绝非京东及非测试保留域名的外部主机。"""

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "data": {"authorizeUrl": "https://evil.example/phish"}})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = JDGoldClient(http)
            with pytest.raises(JDGoldError) as err:
                await client.login()
            assert err.value.code == "INVALID_ENVELOPE"

    asyncio.run(run())


def test_bank_cib_matches_reference_catalog() -> None:
    """兴业银行枚举编码应与参考行情目录一致。"""
    assert Bank.CIB.value == "CIB-JCJ0"
    product = ReferenceProductCatalog.get(Bank.CIB.value)
    assert product.code == "CIB-JCJ0"
    assert product.price_side == "buy"


def test_live_quote_future_anomaly_is_not_promoted_to_normal(tmp_path: Path) -> None:
    """入库时因未来时间戳标记为 ANOMALY 的报价，后续读取不得洗白为 NORMAL。"""

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _req: httpx.Response(200))) as http:
            collector = LiveQuoteCollector(
                JDGoldClient(http),
                ReferenceQuoteRepository(tmp_path),
                enabled=True,
                interval_seconds=60.0,
                stale_seconds=30.0,
            )
            received = datetime(2026, 3, 2, 10, 0, 0, tzinfo=UTC)
            future_quote = ReferenceQuote(
                quote_id="fut-1",
                product_code="CMBC-JCJ",
                price=600.0,
                quote_at=received + timedelta(seconds=15),
                received_at=received,
                available_at=None,
                payload_hash="test_hash",
                quality_state=DataQualityState.ANOMALY,
            )
            # 20 秒后读取，age 为 +5s，仍必须维持 ANOMALY 且 available_at=None
            reclassified = collector._classify(future_quote, received + timedelta(seconds=20))
            assert reclassified.quality_state == DataQualityState.ANOMALY
            assert reclassified.available_at is None

    asyncio.run(run())


def test_data_quality_guard_rejects_nan_inf_and_isolates_products() -> None:
    """DataQualityGuard 拒绝 NaN/Inf、按品种隔离最近报价并限制去重缓存上限。"""
    guard = DataQualityGuard(max_seen_quote_ids=2)
    now = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)

    q_nan = QuoteRaw(
        quote_id="q_nan",
        source_id="s",
        bid_price=math.nan,
        ask_price=600.5,
        quote_at=now,
        received_at=now,
        is_tradable=True,
    )
    q_inf = QuoteRaw(
        quote_id="q_inf",
        source_id="s",
        bid_price=math.inf,
        ask_price=math.inf,
        quote_at=now,
        received_at=now,
        is_tradable=True,
    )
    assert guard.validate_and_classify(q_nan, now=now) == DataQualityState.ANOMALY
    assert guard.validate_and_classify(q_inf, now=now) == DataQualityState.ANOMALY

    # 不同品种价格量级不同，不应互相触发跳价异常
    q_a = QuoteRaw(
        quote_id="q_a",
        source_id="s",
        product_code="PROD_A",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=now,
        received_at=now,
        is_tradable=True,
    )
    q_b = QuoteRaw(
        quote_id="q_b",
        source_id="s",
        product_code="PROD_B",
        bid_price=1000.0,
        ask_price=1001.0,
        quote_at=now,
        received_at=now,
        is_tradable=True,
    )
    assert guard.validate_and_classify(q_a, now=now) == DataQualityState.NORMAL
    assert guard.validate_and_classify(q_b, now=now) == DataQualityState.NORMAL

    # 超出 max_seen_quote_ids 后淘汰最早 ID
    q_c = QuoteRaw(
        quote_id="q_c",
        source_id="s",
        product_code="PROD_A",
        bid_price=600.2,
        ask_price=600.7,
        quote_at=now + timedelta(seconds=1),
        received_at=now + timedelta(seconds=1),
        is_tradable=True,
    )
    assert guard.validate_and_classify(q_c, now=now + timedelta(seconds=1)) == DataQualityState.NORMAL
    assert len(guard._seen_quote_ids) == 2


def test_reconciliation_rejects_nan_and_one_sided_cost_basis() -> None:
    """对账引擎必须将 NaN、单边成本缺失与 client_order_id 冲突判为异常。"""
    service = ReconciliationService()
    snap = AccountSnapshot(cash_available=1000.0, gold_total_g=0.0, cost_basis_per_g=None)

    report_nan, risk_nan = service.reconcile_snapshot(snap, bank_cash=math.nan, bank_gold_g=0.0)
    assert not report_nan.matched
    assert risk_nan.value == "HALT_RECONCILE"

    report_cost, _ = service.reconcile_snapshot(snap, bank_cash=1000.0, bank_gold_g=0.0, bank_cost_basis=600.0)
    assert not report_cost.matched
    assert any(d.field_name == "cost_basis_per_g" for d in report_cost.discrepancies)

    now = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
    local_order = OrderEvent(
        client_order_id="cid_local",
        bank_order_id="bid_1",
        direction=TradeDirection.BUY,
        requested_grams=1.0,
        filled_grams=1.0,
        execution_price=600.0,
        fee=0.0,
        state=OrderState.FILLED,
        received_at=now,
    )
    bank_item = BankStatementItem(
        bank_order_id="bid_1",
        client_order_id="cid_other",
        direction=TradeDirection.BUY,
        grams=1.0,
        price=600.0,
        fee=0.0,
        executed_at=now,
    )
    order_report, _ = service.reconcile_orders([local_order], [bank_item])
    assert not order_report.matched
    assert any("client_order_id" in d.field_name for d in order_report.discrepancies)


def test_baseline_dca_invests_all_installments_without_float_skip() -> None:
    """DCA 定投在 3 期（除不尽）与 11 步（非整除）场景下不得漏投最后一期。"""
    base_t = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
    quotes_15 = [
        QuoteRaw(
            quote_id=f"q_{i}",
            source_id="s",
            bid_price=600.0,
            ask_price=600.0,
            quote_at=base_t + timedelta(hours=i),
            received_at=base_t + timedelta(hours=i),
            is_tradable=True,
        )
        for i in range(15)
    ]
    engine = BaselineEngine(initial_cash=100000.0, fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.0))
    res = engine.run_dca(quotes_15, step_intervals=5)
    # 3 次买入 + 1 次期末清算 = 4 笔交易
    assert res.total_trades == 4
    assert res.final_equity == 100000.0

    # 空行情保留各自策略名称
    assert engine.run_buy_and_hold([]).strategy_name == "BUY_AND_HOLD"
    assert engine.run_dca([]).strategy_name == "DCA"

    with pytest.raises(ValueError):
        BaselineEngine(initial_cash=0.0)


def test_shadow_runner_survives_anomalous_quote_and_buy_slippage() -> None:
    """ShadowRunner 遇到异常报价与买单滑点超额时不得崩溃或污染净值。"""
    now = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
    q_normal = QuoteRaw(
        quote_id="q1",
        source_id="s",
        bid_price=600.0,
        ask_price=600.0,
        quote_at=now,
        received_at=now,
        is_tradable=True,
    )
    q_anomaly = QuoteRaw(
        quote_id="q2",
        source_id="s",
        bid_price=math.nan,
        ask_price=math.nan,
        quote_at=now + timedelta(hours=1),
        received_at=now + timedelta(hours=1),
        is_tradable=True,
        quality_state=DataQualityState.ANOMALY,
    )
    decision = MagicMock(spec=DecisionEngine)
    decision.evaluate_decision.return_value = Decision(
        decision_id="buy_tight_cash",
        decision_at=now,
        action=DecisionAction.BUY,
        delta_g=1.0,
        target_inventory_g=1.0,
        horizon_primary=HoldingPeriod.W1,
    )
    # 现金刚好等于 600.0，叠加默认程序化滑点后成交价 > 600.0，应安全拒单而非抛 ValueError
    runner = ShadowRunner(
        initial_cash=600.0,
        fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.0),
        decision_engine=decision,
    )
    report = runner.run_shadow_simulation([q_normal, q_anomaly], trade_grams_per_order=1.0)
    assert report.final_equity == 600.0
    assert report.total_trades == 0


def test_rfq_graduated_age_penalty_makes_low_prob_rejection_reachable() -> None:
    """接近最大允许报价年龄且点差偏宽的人工单应可触发低概率拒单。"""
    rfq = RFQExecutionEngine(max_quote_age_seconds=10.0)
    now = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
    stale_wide_quote = QuoteRaw(
        quote_id="q_wide",
        source_id="s",
        bid_price=600.0,
        ask_price=602.0,  # > 20bp
        quote_at=now,
        received_at=now + timedelta(seconds=10),
        is_tradable=True,
    )
    req = RFQRequest(
        request_id="req_manual",
        direction=TradeDirection.BUY,
        grams=1.0,
        decision_time=now,
        eligible_at=now + timedelta(seconds=10),
        is_programmatic=False,
    )
    fill = rfq.simulate_execution(req, [stale_wide_quote])
    assert not fill.is_filled
    assert fill.rejection_reason is not None
    assert "LOW_ACCEPTANCE_PROBABILITY" in fill.rejection_reason


def test_execution_adapter_batch_guards_kill_switch_and_replay() -> None:
    """批量挂单必须校验 Kill-Switch、正数限价、幂等冲突与防重复执行。"""
    adapter = ExecutionAdapter()
    open_t = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

    inst = adapter.register_batch_instruction("b1", TradeDirection.BUY, 1.0, 600.0, open_t)
    assert (
        adapter.register_batch_instruction("b1", TradeDirection.BUY, 1.0, 600.0, open_t).instruction_id
        == inst.instruction_id
    )
    with pytest.raises(ValueError):
        adapter.register_batch_instruction("b1", TradeDirection.BUY, 2.0, 600.0, open_t)

    filled, _ = adapter.execute_batch_at_open("b1", 599.0)
    assert filled
    with pytest.raises(ValueError):
        adapter.execute_batch_at_open("b1", 599.0)

    adapter.trigger_kill_switch()
    with pytest.raises(RuntimeError):
        adapter.register_batch_instruction("b2", TradeDirection.BUY, 1.0, 600.0, open_t)


def test_session_calendar_holiday_next_start_and_session_end() -> None:
    """周一节假日应顺延至周二 00:00，周五节假日应使周四交易时段在周五 00:00 结束。"""
    cal_mon = SessionCalendar(holidays=[date(2026, 3, 2)])
    sun = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    next_open = cal_mon.next_session_start(sun)
    assert next_open.date() == date(2026, 3, 3)
    assert next_open.hour == 0
    assert next_open.minute == 0

    cal_fri = SessionCalendar(holidays=[date(2026, 3, 6)])
    thu = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)
    session_end = cal_fri.current_session_end(thu)
    assert session_end is not None
    assert session_end.date() == date(2026, 3, 6)
    assert session_end.hour == 0
    assert session_end.minute == 0


def test_cost_calculator_includes_buy_fee_in_net_exit_loss() -> None:
    """净退出损失公式应计入买入费率。"""
    calc = CostCalculator(FeeRule(buy_fee_rate=0.002, sell_fee_rate=0.004))
    loss = calc.net_exit_loss_per_gram(ask_price=600.0, stop_distance_pct=0.01, spread_pct=0.001)
    assert loss == pytest.approx(600.0 * (0.01 + 0.001 + 0.002 + 0.004))


def test_schemas_and_settings_validation() -> None:
    """校验 Schema 边界与配置归一化。"""
    now = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
    with pytest.raises(ValidationError):
        DecisionQuote(
            quote_id="q1",
            source_id="s",
            bid_price=601.0,
            ask_price=600.0,
            quote_at=now,
            received_at=now,
            is_tradable=True,
        )
    with pytest.raises(ValidationError):
        SnapshotReconcileRequest(bank_cash=-1.0, bank_gold_g=0.0)
    with pytest.raises(ValidationError):
        QuoteIngestRequest(quote_id="   ", source_id="s", bid_price=600.0, ask_price=600.5, quote_at=now)

    settings = Settings(lightgbm_model_dir="   ", news_enabled=False)
    assert settings.lightgbm_model_dir is None
    assert Settings.model_fields["host"].default == "127.0.0.1"


def test_timesfm_predict_feature_with_status_atomic() -> None:
    """TimesFMAgent.predict_feature_with_status 应原子返回错误码。"""
    agent = TimesFMAgent(is_enabled=True, min_context=4)
    now = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
    value, error_code = agent.predict_feature_with_status([], as_of=now)
    assert value is None
    assert error_code == "insufficient_context"


def test_app_lifespan_is_reentrant() -> None:
    """应用 lifespan 多次进入和退出不应遗留已关闭的 HTTP 客户端。"""
    with TestClient(app) as client1:
        assert client1.get("/health").status_code == 200
    with TestClient(app) as client2:
        assert client2.get("/health").status_code == 200


def test_storage_error_maps_to_503_on_quotes_and_paper_routes(tmp_path: Path) -> None:
    """存储层 StorageError 在行情采集与模拟盘下单路由应返回 503 而非 500/409。"""
    from src.contracts.exceptions import StorageError
    from src.runtime.dependencies import RuntimeDependencies

    runtime = RuntimeDependencies(Settings(data_dir=str(tmp_path), live_quotes_enabled=True))
    prev_runtime = getattr(app.state, "runtime", None)
    app.state.runtime = runtime
    try:

        async def fail_collect(_code: str):
            raise StorageError("sqlite locked")

        def fail_submit(**_kwargs):
            raise StorageError("sqlite locked")

        runtime.live_quotes.collect_once = fail_collect  # type: ignore[method-assign]
        runtime.paper.submit_local_order = fail_submit  # type: ignore[method-assign]

        client = TestClient(app, raise_server_exceptions=False)
        res_quote = client.post("/api/v1/quotes/live/collect?product_code=CMBC-JCJ")
        assert res_quote.status_code == 503

        res_paper = client.post(
            "/api/v1/paper/orders",
            json={"engine": "local", "direction": "BUY", "grams": 1.0, "product_code": "CMBC-JCJ"},
        )
        assert res_paper.status_code == 503
    finally:
        asyncio.run(runtime.close())
        app.state.runtime = prev_runtime


def test_bar_collector_parses_8_digit_yyyymmdd_date() -> None:
    """8 位 YYYYMMDD 纯数字日期字符串应被正确解析而非静默丢弃。"""
    from src.services.market.bar_collector import MarketBarCollector

    parsed = MarketBarCollector._parse_datetime("20261126")
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.month == 11
    assert parsed.day == 26


def test_csrf_same_origin_check_rejects_scheme_mismatch(tmp_path: Path) -> None:
    """跨协议同主机 Origin 写请求应被拒绝。"""
    from src.runtime.dependencies import RuntimeDependencies

    runtime = RuntimeDependencies(Settings(data_dir=str(tmp_path), allowed_origins=()))
    prev_runtime = getattr(app.state, "runtime", None)
    app.state.runtime = runtime
    try:
        client = TestClient(app)
        res = client.post(
            "/api/v1/paper/orders",
            headers={"Origin": "https://testserver", "Host": "testserver"},
            json={"engine": "local", "direction": "BUY", "grams": 1.0},
        )
        assert res.status_code == 403
    finally:
        asyncio.run(runtime.close())
        app.state.runtime = prev_runtime
