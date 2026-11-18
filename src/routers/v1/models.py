"""模型状态查询与新闻大模型提取入口。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from src.agents.news import NewsAgentError
from src.contracts.schemas.model import NewsExtractRequest, NewsExtractResponse
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
            "model": runtime.settings.news_llm_model,
            "framework": "pydantic-ai",
        },
    }


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
    except NewsAgentError as exc:
        raise HTTPException(status_code=502, detail="新闻模型提取失败，未录入风险结果") from exc
    try:
        flags = runtime.news.ingest_news(event)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="新闻标识已存在且内容冲突，请使用新标识") from exc
    return NewsExtractResponse(event=event, risk_flags=flags)
