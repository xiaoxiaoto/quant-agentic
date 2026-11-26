"""通过大模型 Agent 提取新闻风险事件，不生成交易指令。"""

import hashlib
import json
import logging
import math
from datetime import UTC, datetime
from typing import Literal

import httpx
import httpx2
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import AgentRunError, UserError
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from src.agents.prompts.template import PromptTemplateManager
from src.contracts.domains.event import NewsEvent


class NewsAgentError(RuntimeError):
    """新闻 Agent 提取失败，调用方不得将其解释为无风险。"""


class NewsRiskExtraction(BaseModel):
    """限制模型只能输出风险分类、评分和摘要。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, str_strip_whitespace=True)

    event_type: Literal["GENERAL", "MACRO", "GEOPOLITICAL", "MONETARY", "MARKET"]
    severity: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=2000)


class _HttpxTransportAdapter(httpx2.AsyncBaseTransport):
    """将 httpx 测试传输层适配为 httpx2 传输接口，避免触发 pydantic-ai 弃用告警。"""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self._transport = transport

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        """转发异步请求并将响应体读取后回传给 httpx2 客户端。"""
        proxied = httpx.Request(
            method=request.method,
            url=str(request.url),
            headers=list(request.headers.raw),
            content=request.content,
        )
        response = await self._transport.handle_async_request(proxied)
        body = await response.aread()
        return httpx2.Response(
            status_code=response.status_code,
            headers=list(response.headers.raw),
            content=body,
        )


class NewsAgent:
    """新闻分析与风险提取 Agent，调用真实模型并保留来源与发布时间。"""

    _prompt_template_name: str = "news_system_prompt"
    _system_prompt: str = PromptTemplateManager.get_prompt_template("news_system_prompt")
    _MAX_TITLE_CHARS: int = 512
    _MAX_CONTENT_CHARS: int = 8000
    _MAX_SOURCE_CHARS: int = 256

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | httpx2.AsyncBaseTransport | None = None,
        system_prompt: str | None = None,
    ) -> None:
        url = httpx2.URL(base_url)
        if (
            url.scheme not in ("http", "https")
            or not url.host
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("新闻模型地址必须为不含凭据和查询参数的 HTTP 接口地址")
        if not model.strip() or not api_key.strip():
            raise ValueError("新闻模型名称和密钥不能为空")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("新闻模型超时必须为正且有限")
        self._model = model
        self._system_prompt = system_prompt or type(self)._system_prompt
        adapted_transport: httpx2.AsyncBaseTransport | None = None
        if isinstance(transport, httpx2.AsyncBaseTransport):
            adapted_transport = transport
        elif transport is not None:
            adapted_transport = _HttpxTransportAdapter(transport)
        self._client = httpx2.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout_seconds,
            transport=adapted_transport,
            follow_redirects=False,
        )

        provider = OpenAIProvider(base_url=base_url, api_key=api_key, http_client=self._client)
        self._agent = Agent(
            OpenAIChatModel(model, provider=provider),
            output_type=NativeOutput(NewsRiskExtraction),
            instructions=self._system_prompt,
            retries=0,
            model_settings={"timeout": timeout_seconds, "max_retries": 0},
        )

    @classmethod
    def get_system_prompt(cls) -> str:
        """从统一提示词管理模块获取新闻提取系统提示词。"""
        return PromptTemplateManager.get_prompt_template(cls._prompt_template_name)

    async def extract(
        self,
        *,
        title: str,
        content: str,
        source: str,
        published_at: datetime,
        event_id: str | None = None,
    ) -> NewsEvent:
        """提取并严格校验结果；失败时抛错，不制造正常风险事件。"""
        if not all(value.strip() for value in (title, content, source)):
            raise ValueError("新闻标题、正文和来源不能为空")
        now = datetime.now(UTC)
        if published_at.utcoffset() is None or published_at > now:
            raise ValueError("新闻发布时间必须包含时区且不得晚于当前时间")
        if event_id is not None and not event_id.strip():
            raise ValueError("新闻事件标识不能为空")
        news_data = {
            "title": title[: self._MAX_TITLE_CHARS],
            "content": content[: self._MAX_CONTENT_CHARS],
            "source": source[: self._MAX_SOURCE_CHARS],
            "published_at": published_at.isoformat(),
        }
        try:
            run = await self._agent.run(json.dumps(news_data, ensure_ascii=False))
            if run.response.finish_reason != "stop":
                raise NewsAgentError("新闻模型输出未完整结束")
            result = run.output
        except (AgentRunError, UserError, TimeoutError, httpx.HTTPError, httpx2.HTTPError) as exc:
            logging.getLogger(__name__).warning("新闻模型调用失败：%s", type(exc).__name__)
            raise NewsAgentError("新闻模型调用或结构化校验失败，请检查服务与配置") from None

        identity = json.dumps(news_data, ensure_ascii=False, sort_keys=True).encode()
        return NewsEvent(
            event_id=event_id or hashlib.sha256(identity).hexdigest(),
            title=title,
            content=content,
            source=source,
            published_at=published_at,
            available_at=datetime.now(UTC),
            event_type=result.event_type,
            severity=result.severity,
            confidence=result.confidence,
            summary=result.summary,
        )

    async def close(self) -> None:
        """关闭共享 HTTP 连接池。"""
        await self._client.aclose()
