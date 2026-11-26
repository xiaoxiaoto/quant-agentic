"""Qlib 离线量化研究、数据导出、表达式因子评估与模型训练 REST API 路由。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from src.contracts.exceptions import QlibDatasetError, QlibUnavailableError
from src.contracts.schemas import (
    QlibExperimentListResponse,
    QlibExportReport,
    QlibExportRequest,
    QlibFactorEvaluateReport,
    QlibFactorEvaluateRequest,
    QlibStatusResponse,
    QlibTrainReport,
    QlibTrainRequest,
)
from src.runtime.dependencies import RuntimeDependencies

router = APIRouter(prefix="/qlib", tags=["qlib"])


@router.get("/status", response_model=QlibStatusResponse)
def get_qlib_status(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> QlibStatusResponse:
    """查询 pyqlib 可用性、版本、本地/外部数据集清单及实验统计。"""
    return runtime.qlib_research.get_status()


@router.post("/export", response_model=QlibExportReport)
def export_qlib_dataset(
    payload: QlibExportRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> QlibExportReport:
    """将本地 SQLite 历史行情（双边报价/参考行情/K 线）按 PIT 导出为 Qlib 二进制目录。"""
    if not runtime.qlib_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="当前已有 Qlib 研究或导出任务正在运行，请稍后重试")
    try:
        target_dir = runtime.resolve_data_path(payload.output_dir) if payload.output_dir is not None else None
        return runtime.qlib_research.export_dataset(payload, target_dir=target_dir)
    except (QlibDatasetError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    finally:
        runtime.qlib_lock.release()


@router.post("/factors/evaluate", response_model=QlibFactorEvaluateReport)
def evaluate_qlib_factors(
    payload: QlibFactorEvaluateRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> QlibFactorEvaluateReport:
    """调用 Qlib 表达式引擎评估因子预设或自定义表达式对扣费净收益的 IC / RankIC。"""
    if not runtime.qlib_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="当前已有 Qlib 研究、导出或训练任务正在运行，请稍后重试")
    try:
        provider_override = (
            runtime.resolve_qlib_provider_path(payload.provider_uri, source_type=payload.source_type)
            if payload.provider_uri is not None
            else None
        )
        return runtime.qlib_research.evaluate_factors(payload, provider_uri_override=provider_override)
    except QlibUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except (QlibDatasetError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    finally:
        runtime.qlib_lock.release()


@router.post("/train", response_model=QlibTrainReport)
def train_qlib_model(
    payload: QlibTrainRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> QlibTrainReport:
    """执行 Qlib 特征提取、Purged Walk-Forward 评估、实验登记并可选导出 QlibAgent 产物。"""
    if not runtime.qlib_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="当前已有 Qlib 研究或训练任务正在运行，请稍后重试")
    try:
        provider_override = (
            runtime.resolve_qlib_provider_path(payload.provider_uri, source_type=payload.source_type)
            if payload.provider_uri is not None
            else None
        )
        model_dir_override = (
            runtime.resolve_data_path(payload.output_model_dir) if payload.output_model_dir is not None else None
        )
        return runtime.qlib_research.train_and_record(
            payload,
            provider_uri_override=provider_override,
            output_model_dir_override=model_dir_override,
        )
    except QlibUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except (QlibDatasetError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    finally:
        runtime.qlib_lock.release()


@router.get("/experiments", response_model=QlibExperimentListResponse)
def list_qlib_experiments(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> QlibExperimentListResponse:
    """按创建时间倒序查询本地登记的 Qlib 实验记录。"""
    records = runtime.qlib_research.list_experiments(limit=limit)
    return QlibExperimentListResponse(total=len(records), experiments=records)
