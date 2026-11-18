"""事件日历与新闻风险领域模型。

用于结构化宏观日程日历与新闻大模型事件提取。
"""

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field


class NewsEvent(BaseModel):
    """结构化新闻事件条目。"""

    event_id: str = Field(
        description="事件唯一全局标识",
    )
    title: str = Field(
        description="新闻事件标题或核心摘要",
    )
    content: str = Field(
        description="新闻正文或事件详情",
    )
    source: str = Field(
        description="新闻发布来源渠道",
    )
    published_at: AwareDatetime = Field(
        description="新闻首发时间戳（PIT 审计使用）",
    )
    available_at: AwareDatetime | None = Field(default=None, description="结构化提取结果实际可用时间")
    summary: str | None = Field(default=None, description="模型提取的新闻摘要")
    event_type: str = Field(
        default="GENERAL",
        description="事件分类（如 MACRO, GEOPOLITICAL, MONETARY）",
    )
    severity: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="事件严重程度评估评分（0.0 至 1.0）",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="事件提取置信度评分（0.0 至 1.0）",
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description="提取出的风控警示标签列表",
    )


class MacroCalendarEvent(BaseModel):
    """预定宏观经济指标与重大事件日程。"""

    event_id: str = Field(
        description="宏观日程项唯一标识",
    )
    event_name: str = Field(
        description="日程事件名称（如 US_CPI, NON_FARM, FOMC）",
    )
    scheduled_at: datetime = Field(
        description="预定公布或召开时间戳",
    )
    lock_window_hours_before: float = Field(
        default=2.0,
        description="公布前禁止或限制开仓的风控锁定小时数",
    )
    lock_window_hours_after: float = Field(
        default=1.0,
        description="公布后持续锁定的风控冷却小时数",
    )
    country: str = Field(
        default="GLOBAL",
        description="事件所属国家或地区",
    )
