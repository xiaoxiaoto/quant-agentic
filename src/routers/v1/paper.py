"""双引擎模拟交易 RESTful 接口路由。

提供本地影子模拟盘与京东黄金远程模拟盘的状态查询、单步评估与手动下单能力。
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import JsonValue

from src.clients.jd_gold import JDGoldError, JDGoldLoginRequiredError
from src.contracts.domains.execution import PaperStepResult
from src.contracts.enums.trading import PaperEngine
from src.contracts.exceptions import StorageError
from src.contracts.schemas import LocalPaperOrderResponse, PaperOrderRequest, PaperStepRequest
from src.runtime.dependencies import RuntimeDependencies

router = APIRouter(prefix="/paper", tags=["paper"])


@router.get("/status")
async def get_paper_status(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    engine: Annotated[PaperEngine, Query(description="模拟盘引擎（local 或 jd）")] = PaperEngine.LOCAL,
) -> dict[str, object] | dict[str, JsonValue]:
    """查询本地影子模拟盘或京东黄金远程模拟盘账户状态。"""
    if engine == PaperEngine.LOCAL:
        return await asyncio.to_thread(runtime.paper.get_local_status)
    try:
        return await runtime.paper.get_jd_status()
    except JDGoldLoginRequiredError:
        raise HTTPException(status_code=401, detail="京东账户尚未授权或登录态已过期，请先完成授权") from None
    except JDGoldError as exc:
        if exc.code == "AUTH_REQUIRED":
            raise HTTPException(status_code=401, detail="京东账户尚未授权或登录态已过期，请先完成授权") from None
        raise HTTPException(status_code=502, detail=f"京东模拟盘接口调用失败（{exc.code}）") from None


@router.post("/step", response_model=PaperStepResult)
def step_local_paper(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    payload: PaperStepRequest | None = None,
) -> PaperStepResult:
    """基于最新 PIT 报价执行单步本地影子模拟交易评估与撮合。"""
    settings = runtime.settings
    product_code = (payload.product_code if payload else None) or settings.default_product_code
    trade_grams = (payload.trade_grams if payload else None) or settings.default_trade_grams
    try:
        return runtime.paper.step_local(product_code=product_code, trade_grams=trade_grams)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/orders", response_model=LocalPaperOrderResponse | JsonValue)
async def submit_paper_order(
    payload: PaperOrderRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> LocalPaperOrderResponse | JsonValue:
    """手动提交本地影子模拟盘或京东远程模拟盘委托。"""
    if payload.engine == PaperEngine.JD:
        try:
            return await runtime.paper.execute_jd_order(
                direction=payload.direction,
                bank=payload.bank,
                grams=payload.grams,
                amount=payload.amount,
                ratio=payload.ratio,
                confirm=payload.confirm,
            )
        except JDGoldLoginRequiredError:
            raise HTTPException(status_code=401, detail="京东账户尚未授权或登录态已过期，请先完成授权") from None
        except JDGoldError as exc:
            if exc.code == "AUTH_REQUIRED":
                raise HTTPException(status_code=401, detail="京东账户尚未授权或登录态已过期，请先完成授权") from None
            raise HTTPException(status_code=502, detail=f"京东模拟盘下单失败（{exc.code}）") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    if payload.grams is None:
        raise HTTPException(status_code=400, detail="本地影子模拟盘手动下单必须指定 grams")
    product_code = payload.product_code or runtime.settings.default_product_code
    try:
        order, fill = await asyncio.to_thread(
            runtime.paper.submit_local_order,
            direction=payload.direction,
            grams=payload.grams,
            product_code=product_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except StorageError:
        raise HTTPException(status_code=503, detail="模拟盘存储暂不可用") from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return LocalPaperOrderResponse(order=order, fill=fill)
