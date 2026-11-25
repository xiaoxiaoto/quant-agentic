"""京东黄金快讯自动同步与新闻风险提取管道服务。

定时或按需拉取京东公开黄金快讯，前置去重后交由 NewsAgent 提取风险属性并写入 NewsRiskEngine。
"""

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from src.agents.news import NewsAgent, NewsAgentError
from src.clients.jd_gold import JDGoldError
from src.contracts.schemas.model import NewsSyncReport
from src.services.jd_gold.gateway import JDGoldGateway
from src.services.risk.news_risk import NewsRiskEngine


class NewsSyncService:
    """京东快讯至新闻风险引擎的异步同步与轮询编排服务。"""

    _SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

    def __init__(
        self,
        jd_gold: JDGoldGateway,
        news_engine: NewsRiskEngine,
        news_agent: NewsAgent | None,
        *,
        enabled: bool = False,
        interval_seconds: float = 300.0,
        page_size: int = 10,
    ) -> None:
        self._jd_gold = jd_gold
        self._news_engine = news_engine
        self._news_agent = news_agent
        self._enabled = enabled
        self._interval = interval_seconds
        self._page_size = page_size
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._last_report: NewsSyncReport | None = None

    @staticmethod
    def _extract_items(payload: JsonValue) -> list[Mapping[str, object]]:
        """从京东快讯响应信封中提取快讯条目字典列表。"""
        body: object = payload
        for key in ("resultData", "datas", "data"):
            if isinstance(body, Mapping) and body.get(key) is not None:
                body = body[key]
        if isinstance(body, Mapping):
            for list_key in (
                "informationList",
                "list",
                "items",
                "flash",
                "records",
                "newsList",
                "datas",
                "data",
            ):
                nested_list = body.get(list_key)
                if isinstance(nested_list, list):
                    body = nested_list
                    break
        if not isinstance(body, list):
            return []
        return [item for item in body if isinstance(item, Mapping)]

    @staticmethod
    def _pick_text(item: Mapping[str, object], keys: tuple[str, ...]) -> str:
        """按候选键顺序提取非空文本字段。"""
        for key in keys:
            raw = item.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return ""

    @classmethod
    def _parse_published_at(cls, item: Mapping[str, object], now: datetime) -> datetime:
        """解析快讯发布时间（支持毫秒/秒时间戳与中文常见时间字符串），不超过当前时刻。"""
        time_keys = (
            "publishTime",
            "publishedAt",
            "pubTime",
            "createTime",
            "releaseTime",
            "timeStamp",
            "time",
            "timestamp",
        )
        for key in time_keys:
            raw = item.get(key)
            if raw is None or isinstance(raw, bool):
                continue
            parsed: datetime | None = None
            if isinstance(raw, (int, float)):
                ts = float(raw)
                if ts > 1e11:
                    ts /= 1000.0
                if ts > 0:
                    parsed = datetime.fromtimestamp(ts, tz=UTC)
            elif isinstance(raw, str):
                text = raw.strip()
                if not text:
                    continue
                if text.isdigit():
                    ts = float(text)
                    if ts > 1e11:
                        ts /= 1000.0
                    if ts > 0:
                        parsed = datetime.fromtimestamp(ts, tz=UTC)
                else:
                    try:
                        dt = datetime.fromisoformat(text)
                        parsed = (
                            dt.astimezone(UTC)
                            if dt.tzinfo is not None
                            else dt.replace(tzinfo=cls._SHANGHAI_TZ).astimezone(UTC)
                        )
                    except ValueError:
                        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                            try:
                                dt = datetime.strptime(text, fmt).replace(tzinfo=cls._SHANGHAI_TZ)
                                parsed = dt.astimezone(UTC)
                                break
                            except ValueError:
                                continue
            if parsed is not None:
                return min(parsed, now)
        return now

    @staticmethod
    def _build_event_id(item: Mapping[str, object], title: str, content: str) -> str:
        """生成稳定的快讯事件幂等标识（优先使用上游 ID 或文章 pageId，否则按内容哈希）。"""
        for key in ("id", "flashId", "newsId", "articleId", "msgId"):
            raw = item.get(key)
            if raw is not None and not isinstance(raw, bool) and str(raw).strip():
                return f"jd_flash_{str(raw).strip()}"
        sub_url = item.get("subUrl")
        if isinstance(sub_url, str) and sub_url.strip():
            page_ids = parse_qs(urlparse(sub_url).query).get("pageId")
            if page_ids and page_ids[0].strip():
                return f"jd_news_{page_ids[0].strip()}"
        digest = hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()[:16]
        return f"jd_flash_{digest}"

    async def _poll(self) -> None:
        logger = logging.getLogger(__name__)
        while True:
            try:
                report = await self.sync_once()
                if report.triggered_risk_flags:
                    logger.warning(
                        "京东快讯同步触发高危新闻风控标志：%s",
                        ", ".join(report.triggered_risk_flags),
                    )
            except asyncio.CancelledError:
                raise
            except (JDGoldError, RuntimeError) as exc:
                logger.warning("京东快讯定时同步跳过或失败：%s", exc)
            except Exception:
                logger.exception("京东快讯定时同步发生未预期异常")
            await asyncio.sleep(self._interval)

    async def sync_once(self, *, page_size: int | None = None) -> NewsSyncReport:
        """执行单次京东黄金快讯拉取、前置去重、LLM 风险提取与入库。"""
        if self._news_agent is None:
            raise RuntimeError("News LLM 尚未启用，无法执行自动快讯风险提取")

        logger = logging.getLogger(__name__)
        size = page_size if page_size is not None else self._page_size
        async with self._lock:
            default_source = "JD_NEWS_FLASH"
            items: list[Mapping[str, object]] = []
            try:
                payload = await self._jd_gold.market.get_news_flash(page_size=size)
                items = self._extract_items(payload)
            except JDGoldError as exc:
                logger.warning("京东快讯接口不可用（%s），自动降级使用京东黄金资讯接口", exc.code)

            if not items:
                fallback_payload = await self._jd_gold.market.get_news(size=size)
                items = self._extract_items(fallback_payload)
                default_source = "JD_GOLD_NEWS"
            now = datetime.now(UTC)

            ingested = 0
            skipped = 0
            failed = 0
            triggered_flags: list[str] = []

            for item in items:
                title = self._pick_text(item, ("title", "headline", "name", "brief"))
                content = self._pick_text(item, ("content", "summary", "desc", "text", "body", "brief"))
                if not title and content:
                    title = content[:80]
                if not content and title:
                    content = title
                if not title or not content:
                    skipped += 1
                    continue

                event_id = self._build_event_id(item, title, content)
                if self._news_engine.has_news_event(event_id) or self._news_engine.has_duplicate_news(title, content):
                    skipped += 1
                    continue

                source = self._pick_text(item, ("source", "media", "origin")) or default_source
                published_at = self._parse_published_at(item, now)

                try:
                    event = await self._news_agent.extract(
                        title=title,
                        content=content,
                        source=source,
                        published_at=published_at,
                        event_id=event_id,
                    )
                    flags = self._news_engine.ingest_news(event)
                    ingested += 1
                    triggered_flags.extend(flags)
                except (NewsAgentError, ValueError) as exc:
                    failed += 1
                    logger.warning("快讯 %s 风险提取或入库失败：%s", event_id, exc)

            report = NewsSyncReport(
                fetched_count=len(items),
                ingested_count=ingested,
                skipped_count=skipped,
                failed_count=failed,
                triggered_risk_flags=sorted(set(triggered_flags)),
                synced_at=datetime.now(UTC),
            )
            self._last_report = report
            return report

    def status(self) -> dict[str, object]:
        """返回快讯自动同步管道运行状态与最近同步摘要。"""
        return {
            "enabled": self._enabled,
            "running": self._task is not None and not self._task.done(),
            "interval_seconds": self._interval,
            "page_size": self._page_size,
            "news_agent_ready": self._news_agent is not None,
            "last_report": self._last_report.model_dump(mode="json") if self._last_report is not None else None,
        }

    async def start(self) -> None:
        """当同步开关与新闻大模型均启用时启动后台轮询。"""
        if self._enabled and self._news_agent is not None and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._poll())

    async def stop(self) -> None:
        """停止后台快讯轮询任务。"""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
