"""实盘辅助闭环与风控控制 RESTful 接口路由。

提供实盘状态总览、单次信号评估、人工急停/恢复及非交易时段次开盘批量挂单管理。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from src.contracts.domains.execution import BatchOrderInstruction, LiveStepResult
from src.contracts.schemas import (
    BatchOrderExecuteRequest,
    BatchOrderExecuteResponse,
    BatchOrderRegisterRequest,
    KillSwitchRequest,
    KillSwitchStatusResponse,
    LiveStepRequest,
)
from src.runtime.dependencies import RuntimeDependencies

router = APIRouter(prefix="/live", tags=["live"])


@router.get("/status")
def get_live_status(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> dict[str, object]:
    """查询实盘风控状态、急停开关、交易时段、提醒统计、账本快照与京东授权摘要。"""
    return runtime.live.get_status()


@router.post("/step", response_model=LiveStepResult)
def evaluate_live_once(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    payload: LiveStepRequest | None = None,
) -> LiveStepResult:
    """执行单次实盘信号评估，并在风控通过时生成对外合规提醒。"""
    settings = runtime.settings
    product_code = (payload.product_code if payload else None) or settings.default_product_code
    trade_grams = (payload.trade_grams if payload else None) or settings.default_trade_grams
    reference_product = payload.reference_product_code if payload else "CMBC-JCJ"
    try:
        return runtime.live.evaluate_once(
            product_code=product_code,
            trade_grams=trade_grams,
            reference_product_code=reference_product,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/halt", response_model=KillSwitchStatusResponse)
def trigger_kill_switch(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    payload: KillSwitchRequest | None = None,
) -> KillSwitchStatusResponse:
    """手动触发实盘人工急停开关。"""
    reason = payload.reason if payload else "MANUAL_EMERGENCY_HALT"
    runtime.live.trigger_kill_switch(reason)
    return KillSwitchStatusResponse(
        kill_switch_active=True,
        message=f"已触发实盘人工急停开关（原因：{reason}）",
    )


@router.post("/resume", response_model=KillSwitchStatusResponse)
def reset_kill_switch(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> KillSwitchStatusResponse:
    """解除人工急停并将实盘风控状态恢复为 NORMAL。"""
    runtime.live.reset_kill_switch()
    return KillSwitchStatusResponse(
        kill_switch_active=False,
        message="已解除人工急停开关，实盘风控状态恢复为 NORMAL",
    )


@router.post("/batch-orders", response_model=BatchOrderInstruction)
def register_batch_order(
    payload: BatchOrderRegisterRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> BatchOrderInstruction:
    """登记非交易时段次开盘批量价挂单指令。"""
    try:
        return runtime.live.register_batch_instruction(
            instruction_id=payload.instruction_id,
            direction=payload.direction,
            grams=payload.grams,
            limit_price=payload.limit_price,
            target_open=payload.target_open,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/batch-orders/{instruction_id}/execute", response_model=BatchOrderExecuteResponse)
def execute_batch_order(
    instruction_id: str,
    payload: BatchOrderExecuteRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> BatchOrderExecuteResponse:
    """次开盘依据银行公布批量成交价核算挂单是否撮合。"""
    try:
        filled, instruction = runtime.live.execute_batch_at_open(
            instruction_id=instruction_id,
            bank_batch_price=payload.bank_batch_price,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"未找到批量挂单指令：{instruction_id}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return BatchOrderExecuteResponse(filled=filled, instruction=instruction)
