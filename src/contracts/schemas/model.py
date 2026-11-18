"""真实模型服务的请求与响应契约。"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from src.contracts.domains.event import NewsEvent


class NewsExtractRequest(BaseModel):
    """来源元数据由调用方提供，模型只提取风险属性。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=1000)
    content: str = Field(min_length=1, max_length=100000)
    source: str = Field(min_length=1, max_length=1000)
    published_at: AwareDatetime
    event_id: str | None = Field(default=None, min_length=1, max_length=200)


class NewsExtractResponse(BaseModel):
    """经模型校验并录入共享风险引擎的新闻结果。"""

    event: NewsEvent
    risk_flags: list[str]
