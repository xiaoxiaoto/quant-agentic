"""银行对账与差异报告 RESTful 接口路由。

执行影子账本与银行官方账目、流水的核对，输出一致性校验结果。
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.services.reconciliation import ReconciliationService
from src.services.shadow_ledger import ShadowLedger
from src.types.domain.account import (
    BankStatementItem,
    OrderEvent,
    ReconciliationReport,
)
from src.types.enums.trading import RiskState

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])
_recon_service = ReconciliationService()
_ledger = ShadowLedger()


class SnapshotReconcileRequest(BaseModel):
    """资产快照对账请求载荷。"""

    bank_cash: float = Field(description="银行官方账户现金余额（元）")
    bank_gold_g: float = Field(description="银行官方黄金持仓总克重（克）")
    bank_cost_basis: float | None = Field(default=None, description="银行官方加权成本单价（元/克）")


class SnapshotReconcileResponse(BaseModel):
    """资产快照对账响应模型。"""

    report: ReconciliationReport = Field(description="对账检查报告")
    recommended_risk_state: RiskState = Field(description="推荐的风控状态（若不一致则为 HALT_RECONCILE）")


class OrdersReconcileRequest(BaseModel):
    """订单流水对账请求载荷。"""

    local_orders: list[OrderEvent] = Field(description="本地订单事件列表")
    bank_statements: list[BankStatementItem] = Field(description="银行官方交易流水记录")


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
