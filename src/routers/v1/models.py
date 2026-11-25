"""模型状态查询与新闻大模型提取入口。"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query

from src.agents.news import NewsAgentError
from src.clients.jd_gold import JDGoldError
from src.contracts.domains.event import MacroCalendarEvent
from src.contracts.schemas import (
    MacroEventCreateRequest,
    NewsExtractRequest,
    NewsExtractResponse,
    NewsOverviewResponse,
    NewsSyncReport,
    TrainingPipelineReport,
    TrainingPipelineRequest,
)
from src.runtime.dependencies import RuntimeDependencies

router = APIRouter(prefix="/models", tags=["models"])


@router.get("/status")
def model_status(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> dict[str, object]:
    """返回实际模型来源，不回传密钥或服务地址。"""
    return {
        "predictor": runtime.model.get_model_info()
        if runtime.model
        else {
            "model_version": "heuristic_v1",
            "is_calibrated": False,
        },
        "timesfm": runtime.timesfm.get_model_info(),
        "news_llm": {
            "enabled": runtime.news_agent is not None,
            "model": runtime.settings.openai_model,
            "framework": "pydantic-ai",
        },
        "news_sync": runtime.news_sync.status(),
    }


@router.get("/news/overview", response_model=NewsOverviewResponse)
def get_news_overview(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> NewsOverviewResponse:
    """查询消息面态势总览：结构化新闻事件、宏观日程及当前生效的新闻风控标志。"""
    now = datetime.now(UTC)
    locked, reason = runtime.news.is_event_locked(now)
    return NewsOverviewResponse(
        event_locked=locked,
        lock_reason=reason,
        active_risk_flags=runtime.news.get_active_risk_flags(now),
        news_events=runtime.news.list_news_events(limit=limit),
        macro_events=runtime.news.list_macro_events(),
    )


@router.post("/news/macro-events", response_model=MacroCalendarEvent)
def register_macro_event(
    payload: MacroEventCreateRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> MacroCalendarEvent:
    """登记重大宏观经济日程（如 CPI / 非农 / 美联储议息），进入窗口自动阻断开仓。"""
    event = MacroCalendarEvent(
        event_id=payload.event_id or f"macro_{uuid4().hex[:10]}",
        event_name=payload.event_name,
        scheduled_at=payload.scheduled_at,
        lock_window_hours_before=payload.lock_window_hours_before,
        lock_window_hours_after=payload.lock_window_hours_after,
    )
    runtime.news.register_macro_event(event)
    return event


@router.post("/news/sync", response_model=NewsSyncReport)
async def sync_jd_news_flash(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    page_size: Annotated[int | None, Query(ge=1, le=50)] = None,
) -> NewsSyncReport:
    """手动触发一次京东黄金快讯拉取、大模型风险提取与风控引擎入库。"""
    if runtime.news_agent is None:
        raise HTTPException(status_code=503, detail="News LLM 尚未启用，无法同步快讯风险")
    try:
        return await runtime.news_sync.sync_once(page_size=page_size)
    except JDGoldError as exc:
        raise HTTPException(status_code=502, detail=f"京东快讯上游拉取失败：{exc.code}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/news/extract", response_model=NewsExtractResponse)
async def extract_news(
    payload: NewsExtractRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> NewsExtractResponse:
    """从原始新闻提取风险并录入与决策共享的事件服务。"""
    if runtime.news_agent is None:
        raise HTTPException(status_code=503, detail="News LLM 尚未启用")
    if payload.published_at > datetime.now(UTC):
        raise HTTPException(status_code=422, detail="新闻发布时间不能晚于当前时间")
    try:
        event = await runtime.news_agent.extract(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NewsAgentError as exc:
        raise HTTPException(status_code=502, detail="新闻模型提取失败，未录入风险结果") from exc
    try:
        flags = runtime.news.ingest_news(event)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="新闻标识已存在且内容冲突，请使用新标识") from exc
    return NewsExtractResponse(event=event, risk_flags=flags)


@router.post("/train", response_model=TrainingPipelineReport)
def run_training_pipeline(
    payload: TrainingPipelineRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> TrainingPipelineReport:
    """从 SQLite 历史行情自动生成 PIT 样本、执行 Walk-Forward 样本外验证并可选训练 LightGBM 产物。"""
    product_code = payload.product_code or runtime.settings.default_product_code
    try:
        return runtime.training_pipeline.run_pipeline(
            product_code=product_code,
            reference_product_code=payload.reference_product_code,
            spread_pct=payload.spread_pct,
            horizons=payload.horizons,
            n_splits=payload.n_splits,
            num_boost_round=payload.num_boost_round,
            confidence_level=payload.confidence_level,
            export_samples_path=payload.export_samples_path,
            output_model_dir=payload.output_model_dir,
            exclude_synthetic=payload.exclude_synthetic,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
