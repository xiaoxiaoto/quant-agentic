"""交易提醒与人工确认交互 RESTful 接口路由。

提供提醒列表查询、详情查看、用户确认意向、拒绝建议及标记已在银行成交等状态流转。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from src.contracts.domains.alert import AlertPayload
from src.contracts.enums.trading import AlertState
from src.contracts.schemas import AlertExecuteRequest
from src.runtime.dependencies import RuntimeDependencies

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertPayload])
def list_alerts(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    state: Annotated[AlertState | None, Query(description="按流转状态过滤")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="返回数量上限")] = 50,
) -> list[AlertPayload]:
    """查询历史交易提醒列表。"""
    return runtime.alerts.list_alerts(state=state, limit=limit)


@router.get("/{alert_id}", response_model=AlertPayload)
def get_alert_detail(
    alert_id: str,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> AlertPayload:
    """查询指定提醒条目的完整合规载荷与状态。"""
    alert = runtime.alerts.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="未找到指定的提醒条目")
    return alert


@router.post("/{alert_id}/confirm", response_model=AlertPayload)
def confirm_alert(
    alert_id: str,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> AlertPayload:
    """用户确认交易意图。"""
    try:
        return runtime.alerts.confirm_alert(alert_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到指定的提醒条目") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/{alert_id}/decline", response_model=AlertPayload)
def decline_alert(
    alert_id: str,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> AlertPayload:
    """用户拒绝交易建议。"""
    try:
        return runtime.alerts.decline_alert(alert_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到指定的提醒条目") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/{alert_id}/execute", response_model=AlertPayload)
def mark_executed(
    alert_id: str,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    payload: AlertExecuteRequest | None = None,
) -> AlertPayload:
    """用户标记已在银行官方界面完成实际成交；提供成交价与克重时同步登记影子账本。"""
    try:
        if payload is not None:
            return runtime.live.mark_executed(
                alert_id,
                filled_grams=payload.filled_grams,
                filled_price=payload.filled_price,
                fee=payload.fee,
                bank_order_id=payload.bank_order_id,
            )
        return runtime.alerts.mark_executed(alert_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到指定的提醒条目") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
