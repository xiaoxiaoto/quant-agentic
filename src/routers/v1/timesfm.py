"""TimesFM 3.0 多因子模型数据准备、训练、严格样本外回测与导出 REST API 路由。"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from src.contracts.exceptions import TimesFMDatasetError, TimesFMTrainingError
from src.contracts.schemas import (
    TimesFMBacktestReport,
    TimesFMBacktestRequest,
    TimesFMExperimentListResponse,
    TimesFMExportReport,
    TimesFMExportRequest,
    TimesFMFullPipelineReport,
    TimesFMFullPipelineRequest,
    TimesFMPrepareReport,
    TimesFMPrepareRequest,
    TimesFMStatusResponse,
    TimesFMTrainReport,
    TimesFMTrainRequest,
)
from src.runtime.dependencies import RuntimeDependencies

router = APIRouter(prefix="/timesfm", tags=["timesfm"])

Runtime = Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)]


def _safe(runtime: RuntimeDependencies, raw: str | None) -> str | None:
    """将读取路径约束在 DATA_DIR 内，越界抛出 ValueError。"""
    return None if raw is None else str(runtime.resolve_data_path(raw))


def _safe_output(runtime: RuntimeDependencies, raw: str | None) -> str | None:
    """将输出/导出路径约束为 DATA_DIR 内的非保留子目录，越界或指向根目录抛出 ValueError。"""
    return None if raw is None else str(runtime.resolve_output_subdir(raw))


def _run_locked[T](runtime: RuntimeDependencies, action: Callable[[], T]) -> T:
    """获取 TimesFM 任务锁后执行动作，并将领域异常映射为 HTTP 状态码。"""
    if not runtime.timesfm_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="当前已有 TimesFM 任务正在运行，请稍后重试")
    try:
        return action()
    except (TimesFMDatasetError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except TimesFMTrainingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    finally:
        runtime.timesfm_lock.release()


@router.get("/status", response_model=TimesFMStatusResponse)
def get_timesfm_status(runtime: Runtime) -> TimesFMStatusResponse:
    """查询 TimesFM 多因子研究运行时、最近数据集与实验统计。"""
    return runtime.timesfm_pipeline.get_status()


@router.post("/prepare", response_model=TimesFMPrepareReport)
def prepare_timesfm_dataset(payload: TimesFMPrepareRequest, runtime: Runtime) -> TimesFMPrepareReport:
    """按 PIT 构造 TimesFM 多因子数据集。"""
    return _run_locked(
        runtime,
        lambda: runtime.timesfm_pipeline.prepare_dataset(
            payload.model_copy(update={"output_dir": _safe_output(runtime, payload.output_dir)})
        ),
    )


@router.post("/train", response_model=TimesFMTrainReport)
def train_timesfm_model(payload: TimesFMTrainRequest, runtime: Runtime) -> TimesFMTrainReport:
    """训练融合头、可选微调主干并执行 Walk-Forward 样本外验证。"""
    return _run_locked(
        runtime,
        lambda: runtime.timesfm_pipeline.train_and_record(
            payload.model_copy(
                update={
                    "dataset_dir": _safe(runtime, payload.dataset_dir),
                    "output_model_dir": _safe_output(runtime, payload.output_model_dir),
                }
            )
        ),
    )


@router.post("/backtest", response_model=TimesFMBacktestReport)
def backtest_timesfm_model(payload: TimesFMBacktestRequest, runtime: Runtime) -> TimesFMBacktestReport:
    """执行严格样本外 PIT 回测（预测层指标与交易层影子仿真）。"""
    return _run_locked(
        runtime,
        lambda: runtime.timesfm_pipeline.run_backtest(
            payload.model_copy(
                update={
                    "model_dir": _safe(runtime, payload.model_dir),
                    "dataset_dir": _safe(runtime, payload.dataset_dir),
                }
            )
        ),
    )


@router.post("/export", response_model=TimesFMExportReport)
def export_timesfm_model(payload: TimesFMExportRequest, runtime: Runtime) -> TimesFMExportReport:
    """导出不可变模型包并写入 SHA-256 校验。"""
    return _run_locked(
        runtime,
        lambda: runtime.timesfm_pipeline.export_model(
            payload.model_copy(update={"output_model_dir": _safe_output(runtime, payload.output_model_dir)})
        ),
    )


@router.post("/pipeline", response_model=TimesFMFullPipelineReport)
def run_timesfm_pipeline(payload: TimesFMFullPipelineRequest, runtime: Runtime) -> TimesFMFullPipelineReport:
    """一键串联数据准备、训练、样本外回测与可选导出。"""

    def action() -> TimesFMFullPipelineReport:
        safe = payload.model_copy(
            update={
                "prepare": payload.prepare.model_copy(
                    update={"output_dir": _safe_output(runtime, payload.prepare.output_dir)}
                ),
                "train": payload.train.model_copy(update={"output_model_dir": None, "dataset_dir": None}),
                "backtest": payload.backtest.model_copy(update={"model_dir": None, "dataset_dir": None}),
                "output_model_dir": _safe_output(runtime, payload.output_model_dir),
            }
        )
        return runtime.timesfm_pipeline.run_full_pipeline(safe)

    return _run_locked(runtime, action)


@router.get("/experiments", response_model=TimesFMExperimentListResponse)
def list_timesfm_experiments(
    runtime: Runtime,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TimesFMExperimentListResponse:
    """按创建时间倒序查询 TimesFM 去重实验记录。"""
    records = runtime.timesfm_pipeline.list_experiments(limit=limit)
    return TimesFMExperimentListResponse(
        total=runtime.timesfm_pipeline.count_experiments(),
        experiments=records,
    )
