"""交易提醒与人工确认交互 RESTful 接口路由。

提供提醒列表查询、详情查看、用户确认意向、拒绝建议及标记已在银行成交等状态流转。
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from src.contracts.domains.alert import AlertPayload
from src.contracts.enums.trading import AlertState
from src.services.risk.alert_service import AlertService

router = APIRouter(prefix="/alerts", tags=["alerts"])
_alert_service = AlertService()


@router.get("", response_model=list[AlertPayload])
def list_alerts(
    state: Annotated[AlertState | None, Query(description="按流转状态过滤")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="返回数量上限")] = 50,
) -> list[AlertPayload]:
    """查询历史交易提醒列表。"""
    return _alert_service.list_alerts(state=state, limit=limit)


@router.get("/{alert_id}", response_model=AlertPayload)
def get_alert_detail(alert_id: str) -> AlertPayload:
    """查询指定提醒条目的完整合规载荷与状态。"""
    alert = _alert_service.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="未找到指定的提醒条目")
    return alert


@router.post("/{alert_id}/confirm", response_model=AlertPayload)
def confirm_alert(alert_id: str) -> AlertPayload:
    """用户确认交易意图。"""
    try:
        return _alert_service.confirm_alert(alert_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到指定的提醒条目")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{alert_id}/decline", response_model=AlertPayload)
def decline_alert(alert_id: str) -> AlertPayload:
    """用户拒绝交易建议。"""
    try:
        return _alert_service.decline_alert(alert_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到指定的提醒条目")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{alert_id}/execute", response_model=AlertPayload)
def mark_executed(alert_id: str) -> AlertPayload:
    """用户标记已在银行官方界面完成实际成交。"""
    try:
        return _alert_service.mark_executed(alert_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到指定的提醒条目")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
