"""银行对账与差异报告 RESTful 接口路由。

执行影子账本与银行官方账目、流水的核对，输出一致性校验结果。
"""

from fastapi import APIRouter

from src.services.reconciliation import ReconciliationService
from src.services.shadow_ledger import ShadowLedger
from src.types.schemas import (
    OrdersReconcileRequest,
    SnapshotReconcileRequest,
    SnapshotReconcileResponse,
)

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])
_recon_service = ReconciliationService()
_ledger = ShadowLedger()


@router.post("/snapshot", response_model=SnapshotReconcileResponse)
def reconcile_snapshot(payload: SnapshotReconcileRequest) -> SnapshotReconcileResponse:
    """核对当前本地影子账本快照与银行官方资产。"""
    local_snap = _ledger.take_snapshot()
    report, risk = _recon_service.reconcile_snapshot(
        local_snapshot=local_snap,
        bank_cash=payload.bank_cash,
        bank_gold_g=payload.bank_gold_g,
        bank_cost_basis=payload.bank_cost_basis,
    )
    return SnapshotReconcileResponse(
        report=report,
        recommended_risk_state=risk,
    )


@router.post("/orders", response_model=SnapshotReconcileResponse)
def reconcile_orders(payload: OrdersReconcileRequest) -> SnapshotReconcileResponse:
    """逐笔核对本地订单流水与银行官方交易流水。"""
    report, risk = _recon_service.reconcile_orders(
        local_orders=payload.local_orders,
        bank_statements=payload.bank_statements,
    )
    return SnapshotReconcileResponse(
        report=report,
        recommended_risk_state=risk,
    )
