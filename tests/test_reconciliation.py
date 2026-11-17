"""对账服务与差异报警单元测试。"""

from datetime import UTC, datetime

from src.services.reconciliation import ReconciliationService
from src.types.domain.account import (
    AccountSnapshot,
    BankStatementItem,
    OrderEvent,
)
from src.types.enums.trading import OrderState, RiskState, TradeDirection


def test_reconcile_snapshot_matched() -> None:
    recon = ReconciliationService()
    now_dt = datetime.now(UTC)
    snap = AccountSnapshot(
        cash_available=44000.0,
        gold_total_g=10.0,
        cost_basis_per_g=600.0,
        as_of=now_dt,
    )
    report, risk = recon.reconcile_snapshot(
        local_snapshot=snap,
        bank_cash=44000.005,  # 在 0.01 容差内
        bank_gold_g=10.00005,  # 在 0.0001 容差内
        bank_cost_basis=600.005,  # 在 0.01 容差内
    )
    assert report.matched is True
    assert len(report.discrepancies) == 0
    assert risk == RiskState.NORMAL


def test_reconcile_snapshot_discrepancy_triggers_halt() -> None:
    recon = ReconciliationService()
    now_dt = datetime.now(UTC)
    snap = AccountSnapshot(
        cash_available=44000.0,
        gold_total_g=10.0,
        cost_basis_per_g=600.0,
        as_of=now_dt,
    )
    # 银行现金少 100 元，克重少 1g
    report, risk = recon.reconcile_snapshot(
        local_snapshot=snap,
        bank_cash=43900.0,
        bank_gold_g=9.0,
    )
    assert report.matched is False
    assert risk == RiskState.HALT_RECONCILE
    assert len(report.discrepancies) == 2
    breached_fields = [d.field_name for d in report.discrepancies]
    assert "cash_available" in breached_fields
    assert "gold_total_g" in breached_fields


def test_reconcile_orders_matched() -> None:
    recon = ReconciliationService()
    now_dt = datetime.now(UTC)

    local_order = OrderEvent(
        client_order_id="cid_1001",
        bank_order_id="bid_2001",
        direction=TradeDirection.BUY,
        requested_grams=10.0,
        filled_grams=10.0,
        execution_price=600.0,
        fee=0.0,
        state=OrderState.FILLED,
    )
    bank_item = BankStatementItem(
        bank_order_id="bid_2001",
        client_order_id="cid_1001",
        direction=TradeDirection.BUY,
        grams=10.0,
        price=600.0,
        fee=0.0,
        executed_at=now_dt,
    )

    report, risk = recon.reconcile_orders(
        local_orders=[local_order],
        bank_statements=[bank_item],
    )
    assert report.matched is True
    assert risk == RiskState.NORMAL
    assert len(report.discrepancies) == 0


def test_reconcile_orders_unmatched_and_discrepancies() -> None:
    recon = ReconciliationService()
    now_dt = datetime.now(UTC)

    # 价格不一致与缺失订单
    local_order = OrderEvent(
        client_order_id="cid_1",
        bank_order_id="bid_1",
        direction=TradeDirection.BUY,
        requested_grams=10.0,
        filled_grams=10.0,
        execution_price=600.0,
        state=OrderState.FILLED,
    )
    bank_item = BankStatementItem(
        bank_order_id="bid_1",
        client_order_id="cid_1",
        direction=TradeDirection.BUY,
        grams=10.0,
        price=605.0,  # 差异 5 元/g
        executed_at=now_dt,
    )
    bank_extra = BankStatementItem(
        bank_order_id="bid_extra",
        direction=TradeDirection.SELL,
        grams=2.0,
        price=610.0,
        executed_at=now_dt,
    )

    report, risk = recon.reconcile_orders(
        local_orders=[local_order],
        bank_statements=[bank_item, bank_extra],
    )
    assert report.matched is False
    assert risk == RiskState.HALT_RECONCILE
    assert len(report.discrepancies) >= 1
    assert "bid_extra" in report.unmatched_bank_orders
