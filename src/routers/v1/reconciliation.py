"""银行对账与差异报告 RESTful 接口路由。

执行影子账本与银行官方账目、流水的核对，输出一致性校验结果。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from src.clients.jd_gold import JDGoldError, JDGoldLoginRequiredError
from src.contracts.domains.account import ReconciliationReport
from src.contracts.schemas import (
    OrdersReconcileRequest,
    SnapshotReconcileRequest,
    SnapshotReconcileResponse,
)
from src.runtime.dependencies import RuntimeDependencies

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.post("/snapshot", response_model=SnapshotReconcileResponse)
def reconcile_snapshot(
    payload: SnapshotReconcileRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> SnapshotReconcileResponse:
    """核对当前本地影子账本快照与银行官方资产。"""
    local_snap = runtime.ledger.take_snapshot()
    report, risk = runtime.reconciliation.reconcile_snapshot(
        local_snapshot=local_snap,
        bank_cash=payload.bank_cash,
        bank_gold_g=payload.bank_gold_g,
        bank_cost_basis=payload.bank_cost_basis,
    )
    runtime.state_store.save_reconciliation_report(report)
    return SnapshotReconcileResponse(
        report=report,
        recommended_risk_state=risk,
    )


@router.post("/orders", response_model=SnapshotReconcileResponse)
def reconcile_orders(
    payload: OrdersReconcileRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> SnapshotReconcileResponse:
    """逐笔核对本地订单流水与银行官方交易流水。"""
    report, risk = runtime.reconciliation.reconcile_orders(
        local_orders=list(payload.local_orders),
        bank_statements=list(payload.bank_statements),
    )
    runtime.state_store.save_reconciliation_report(report)
    return SnapshotReconcileResponse(
        report=report,
        recommended_risk_state=risk,
    )


@router.post("/jd-holdings", response_model=SnapshotReconcileResponse)
async def reconcile_with_jd_holdings(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    bank_cash: float | None = Query(default=None, ge=0, description="可选银行现金余额（未提供时以本地现金为基准）"),
) -> SnapshotReconcileResponse:
    """拉取京东黄金实盘持仓并自动与本地影子账本对账，差异时自动熔断。"""
    try:
        report, risk = await runtime.live.reconcile_with_jd_holdings(bank_cash=bank_cash)
    except JDGoldLoginRequiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    except JDGoldError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return SnapshotReconcileResponse(
        report=report,
        recommended_risk_state=risk,
    )


@router.get("/reports", response_model=list[ReconciliationReport])
def list_reconciliation_reports(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ReconciliationReport]:
    """按时间倒序查询持久化对账审计报告。"""
    return runtime.live.list_reconciliation_reports(limit=limit)
