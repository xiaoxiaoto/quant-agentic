"""京东资讯与持仓采集的读写契约。"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, JsonValue


class JDNewsArticle(BaseModel):
    """已落库的京东资讯原文及来源。"""

    article_id: str
    event_id: str
    channel: Literal["flash", "news", "morning"]
    title: str
    content: str
    source: str
    published_at: AwareDatetime
    collected_at: AwareDatetime
    raw: dict[str, JsonValue]


class JDHoldingsSnapshot(BaseModel):
    """当前授权对应的京东实盘持仓快照。"""

    data: dict[str, JsonValue] | None = None
    collected_at: AwareDatetime | None = None
    last_success_at: AwareDatetime | None = None
    status: Literal["pending", "fresh", "stale", "auth_required"]
