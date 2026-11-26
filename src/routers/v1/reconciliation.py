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
    """核对当前本地影子账本快照与银行官方资产，差异时自动熔断并触发急停。"""
    report, risk = runtime.live.reconcile_snapshot(
        bank_cash=payload.bank_cash,
        bank_gold_g=payload.bank_gold_g,
        bank_cost_basis=payload.bank_cost_basis,
    )
    return SnapshotReconcileResponse(
        report=report,
        recommended_risk_state=risk,
    )


@router.post("/orders", response_model=SnapshotReconcileResponse)
def reconcile_orders(
    payload: OrdersReconcileRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> SnapshotReconcileResponse:
    """逐笔核对本地订单流水与银行官方交易流水，差异时自动熔断并触发急停。"""
    report, risk = runtime.live.reconcile_orders(
        local_orders=list(payload.local_orders),
        bank_statements=list(payload.bank_statements),
    )
    return SnapshotReconcileResponse(
        report=report,
        recommended_risk_state=risk,
    )


@router.post("/jd-holdings", response_model=SnapshotReconcileResponse)
async def reconcile_with_jd_holdings(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    bank_cash: float | None = Query(
        default=None, ge=0, allow_inf_nan=False, description="可选银行现金余额（未提供时以本地现金为基准）"
    ),
) -> SnapshotReconcileResponse:
    """拉取京东黄金实盘持仓并自动与本地影子账本对账，差异时自动熔断。"""
    try:
        report, risk = await runtime.live.reconcile_with_jd_holdings(bank_cash=bank_cash)
    except JDGoldLoginRequiredError:
        raise HTTPException(
            status_code=401,
            detail="京东账户尚未授权或登录态已过期，请先发起京东登录授权",
        ) from None
    except JDGoldError as exc:
        if exc.code == "AUTH_REQUIRED":
            raise HTTPException(
                status_code=401,
                detail="京东账户尚未授权或登录态已过期，请先发起京东登录授权",
            ) from None
        raise HTTPException(status_code=502, detail=f"京东上游接口调用失败（{exc.code}）") from None
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
