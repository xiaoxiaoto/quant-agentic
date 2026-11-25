"""影子账本资产与流水 RESTful 接口路由。

提供影子账本资产快照、持仓成本、订单流水查询及一键按京东实盘持仓初始化。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from src.clients.jd_gold import JDGoldError, JDGoldLoginRequiredError
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
    offset: Annotated[int, Query(ge=0, description="分页偏移量")] = 0,
    limit: Annotated[int | None, Query(ge=1, le=500, description="分页条数，留空返回全部")] = None,
) -> list[OrderEvent]:
    """获取本地影子账本记录的订单历史流水（支持分页）。"""
    return runtime.ledger.get_order_history(offset=offset, limit=limit)


@router.post("/init-from-jd", response_model=AccountSnapshot)
async def init_ledger_from_jd_holdings(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    initial_cash: Annotated[
        float | None,
        Query(gt=0, description="初始化基准总资金（元），留空时自动使用配置项 DEFAULT_INITIAL_CASH"),
    ] = None,
) -> AccountSnapshot:
    """按京东实盘持仓一键重置并初始化本地影子账本（可用现金 = 初始资金 - 持仓成本）。"""
    try:
        return await runtime.live.initialize_ledger_from_jd_holdings(initial_cash=initial_cash)
    except JDGoldLoginRequiredError:
        raise HTTPException(status_code=401, detail="京东账户未登录或登录态已过期，请先完成授权") from None
    except JDGoldError as exc:
        raise HTTPException(status_code=502, detail=f"拉取京东持仓失败：{exc.code}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
