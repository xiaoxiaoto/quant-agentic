"""影子账本资产与流水 RESTful 接口路由。

提供影子账本资产快照、持仓成本与订单流水查询。
"""

from fastapi import APIRouter

from src.services.shadow_ledger import ShadowLedger
from src.types.domain.account import AccountSnapshot, OrderEvent

router = APIRouter(prefix="/ledger", tags=["ledger"])
_ledger = ShadowLedger()


@router.get("/snapshot", response_model=AccountSnapshot)
def get_account_snapshot() -> AccountSnapshot:
    """获取当前影子账本资产与持仓成本快照。"""
    return _ledger.take_snapshot()


@router.get("/orders", response_model=list[OrderEvent])
def get_order_history() -> list[OrderEvent]:
    """获取本地影子账本记录的全部订单历史流水。"""
    return _ledger.get_order_history()
