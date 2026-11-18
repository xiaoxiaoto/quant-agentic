"""影子账本资产与流水 RESTful 接口路由。

提供影子账本资产快照、持仓成本与订单流水查询。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from src.contracts.domains.account import AccountSnapshot, OrderEvent
from src.runtime.dependencies import RuntimeDependencies

router = APIRouter(prefix="/ledger", tags=["ledger"])


@router.get("/snapshot", response_model=AccountSnapshot)
def get_account_snapshot(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> AccountSnapshot:
    """获取当前影子账本资产与持仓成本快照。"""
    return runtime.ledger.take_snapshot()


@router.get("/orders", response_model=list[OrderEvent])
def get_order_history(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> list[OrderEvent]:
    """获取本地影子账本记录的全部订单历史流水。"""
    return runtime.ledger.get_order_history()
