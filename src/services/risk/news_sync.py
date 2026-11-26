"""京东黄金快讯自动同步与新闻风险提取管道服务。

定时或按需拉取京东公开黄金快讯，前置去重后交由 NewsAgent 提取风险属性并写入 NewsRiskEngine。
"""

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Literal, cast
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from src.agents.news import NewsAgent, NewsAgentError
from src.clients.jd_gold import JDGoldError
from src.contracts.domains.jd_collection import JDNewsArticle
from src.contracts.exceptions import StorageError
from src.contracts.schemas.model import NewsSyncReport
from src.repositories.database import SQLiteDatabase
from src.repositories.jd_collection import JDCollectionRepository
from src.services.jd_gold.gateway import JDGoldGateway
from src.services.risk.news_risk import NewsRiskEngine


class NewsSyncService:
    """京东快讯至新闻风险引擎的异步同步与轮询编排服务。"""

    _SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
    _MAX_FLASH_PAGES = 20

    def __init__(
        self,
        jd_gold: JDGoldGateway,
        news_engine: NewsRiskEngine,
        news_agent: NewsAgent | None,
        repository: JDCollectionRepository,
        *,
        enabled: bool = False,
        interval_seconds: float = 300.0,
        page_size: int = 10,
    ) -> None:
        self._jd_gold = jd_gold
        self._news_engine = news_engine
        self._news_agent = news_agent
        self._repository = repository
        self._enabled = enabled
        self._interval = interval_seconds
        self._page_size = page_size
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._risk_lock = asyncio.Lock()
        self._risk_task: asyncio.Task[None] | None = None
        self._last_report: NewsSyncReport | None = None
        self._errors: dict[str, str] = {}

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
                "morningReportList",
                "reportList",
                "newsFlashList",
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
        if not isinstance(body, list) or not all(isinstance(item, Mapping) for item in body):
            raise ValueError("京东资讯列表结构无效")
        return cast(list[Mapping[str, object]], body)

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
                poll_report = await self.sync_once(all_channels=True, extract=False)
                if self._news_agent is not None and (self._risk_task is None or self._risk_task.done()):
                    self._risk_task = asyncio.create_task(self._process_backlog(poll_report))
            except asyncio.CancelledError:
                raise
            except (JDGoldError, RuntimeError) as exc:
                logger.warning("京东快讯定时同步跳过或失败：%s", exc)
            except Exception:
                logger.exception("京东快讯定时同步发生未预期异常")
            await asyncio.sleep(self._interval)

    def _articles(
        self, items: list[Mapping[str, object]], channel: Literal["flash", "news", "morning"]
    ) -> list[JDNewsArticle]:
        now = datetime.now(UTC)
        articles: list[JDNewsArticle] = []
        for item in items:
            title = self._pick_text(item, ("title", "headline", "name", "brief", "reportTitle"))
            content = self._pick_text(item, ("content", "summary", "desc", "text", "body", "brief", "reportContent"))
            if not title and not content:
                continue
            title = title or content[:80]
            content = content or title
            event_id = self._build_event_id(item, title, content)
            if channel != "flash" and not (channel == "news" and "_news_" in event_id):
                event_id = event_id.replace("jd_flash_", f"jd_{channel}_", 1)
            articles.append(
                JDNewsArticle(
                    article_id=f"{channel}:{event_id}",
                    event_id=event_id,
                    channel=channel,
                    title=title,
                    content=content,
                    source=self._pick_text(item, ("source", "media", "origin")) or f"JD_{channel.upper()}",
                    published_at=self._parse_published_at(item, now).astimezone(UTC),
                    collected_at=now,
                    raw=cast(dict[str, JsonValue], dict(item)),
                )
            )
        return articles

    async def _collect_flash(self, size: int) -> list[JDNewsArticle]:
        checkpoint = await asyncio.to_thread(self._repository.checkpoint, "flash")
        resume = await asyncio.to_thread(self._repository.flash_scan)
        head, cursor = resume if resume is not None else ("", "")
        used_cursors = {cursor}
        articles: list[JDNewsArticle] = []
        for _ in range(self._MAX_FLASH_PAGES):
            payload = await self._jd_gold.market.get_news_flash(page_size=size, last_id=cursor)
            items = self._extract_items(payload)
            page = self._articles(items, "flash")
            articles.extend(page)
            if items and not page:
                raise ValueError("京东快讯缺少可识别条目")
            if not head and page:
                head = page[0].article_id
            new_page: list[JDNewsArticle] = []
            reached_checkpoint = False
            for item in page:
                if checkpoint is not None and item.article_id == checkpoint:
                    reached_checkpoint = True
                    break
                new_page.append(item)
            if len(items) < size or reached_checkpoint:
                if checkpoint is not None and head and not reached_checkpoint:
                    logging.getLogger(__name__).warning("京东快讯上游窗口内未找到上次进度，可能存在采集缺口")
                await SQLiteDatabase.run_shielded_write(
                    self._repository.save_articles, new_page, checkpoint=head or None
                )
                return articles

            raw_cursor = items[-1].get("id") or items[-1].get("flashId")
            if not isinstance(raw_cursor, (int, str)) or isinstance(raw_cursor, bool):
                raise ValueError("京东快讯分页缺少游标")
            cursor = str(raw_cursor)
            if cursor in used_cursors:
                raise ValueError("京东快讯分页游标重复")
            used_cursors.add(cursor)
            await SQLiteDatabase.run_shielded_write(self._repository.save_articles, page, flash_scan=(head, cursor))

        # 扫满单轮页数上限仍未命中旧进度时，推进 checkpoint 至最新头部并清理游标，防止下轮饥饿实时快讯
        if head:
            logging.getLogger(__name__).warning("京东快讯达到单轮分页上限，推进最新进度以保障实时采集")
            await SQLiteDatabase.run_shielded_write(self._repository.save_articles, [], checkpoint=head)
        return articles

    async def _collect_channel(self, channel: Literal["flash", "news", "morning"], size: int) -> list[JDNewsArticle]:
        try:
            if channel == "flash":
                articles = await self._collect_flash(size)
            else:
                payload = (
                    await self._jd_gold.market.get_news(size=size)
                    if channel == "news"
                    else await self._jd_gold.market.get_morning_report(page_size=size)
                )
                articles = self._articles(self._extract_items(payload), channel)
                await SQLiteDatabase.run_shielded_write(self._repository.save_articles, articles)
            self._errors.pop(channel, None)
            return articles
        except (JDGoldError, ValueError, OverflowError, OSError, StorageError) as exc:
            self._errors[channel] = exc.code if isinstance(exc, JDGoldError) else str(exc)
            logging.getLogger(__name__).warning("京东资讯 %s 采集失败（%s）", channel, self._errors[channel])
            return []

    async def _extract_risk(self, articles: list[JDNewsArticle], size: int) -> tuple[int, int, int, list[str]]:
        logger = logging.getLogger(__name__)
        ingested = skipped = failed = 0
        flags: list[str] = []
        if self._news_agent is None:
            return ingested, skipped, failed, flags
        async with self._risk_lock:
            unique = {article.article_id: article for article in articles}
            unprocessed_ids = (
                await asyncio.to_thread(self._repository.filter_unprocessed_ids, set(unique)) if unique else set()
            )
            skipped += sum(1 for aid in unique if aid not in unprocessed_ids)
            pending = await asyncio.to_thread(self._repository.pending_articles, size, exclude=set(unique))
            candidates = [*pending, *(article for aid, article in unique.items() if aid in unprocessed_ids)]
            processed_ids: list[str] = []
            attempt_ids: list[str] = []
            try:
                for article in candidates:
                    try:
                        if self._news_engine.has_news_event(article.event_id) or self._news_engine.has_duplicate_news(
                            article.title, article.content
                        ):
                            skipped += 1
                        else:
                            event = await self._news_agent.extract(
                                title=article.title,
                                content=article.content,
                                source=article.source,
                                published_at=article.published_at,
                                event_id=article.event_id,
                            )
                            flags.extend(await SQLiteDatabase.run_shielded_write(self._news_engine.ingest_news, event))
                            ingested += 1
                        processed_ids.append(article.article_id)
                    except ValueError as exc:
                        failed += 1
                        if self._news_engine.has_news_event(article.event_id):
                            # 标识永久冲突，标记为已处理避免无限重试
                            processed_ids.append(article.article_id)
                        else:
                            attempt_ids.append(article.article_id)
                        logger.warning("资讯 %s 风险提取或入库校验失败：%s", article.event_id, exc)
                    except (NewsAgentError, OSError, StorageError) as exc:
                        failed += 1
                        attempt_ids.append(article.article_id)
                        logger.warning("资讯 %s 风险提取或入库失败：%s", article.event_id, exc)
            finally:
                if processed_ids:
                    try:
                        await SQLiteDatabase.run_shielded_write(self._repository.mark_processed_batch, processed_ids)
                    except (OSError, StorageError):
                        logger.warning("批量标记已处理资讯失败（%d 条）", len(processed_ids))
                if attempt_ids:
                    try:
                        await SQLiteDatabase.run_shielded_write(
                            self._repository.mark_attempts_batch, attempt_ids, datetime.now(UTC)
                        )
                    except (OSError, StorageError):
                        logger.warning("批量记录资讯尝试时间失败（%d 条）", len(attempt_ids))
        return ingested, skipped, failed, sorted(set(flags))

    async def _process_backlog(self, target_report: NewsSyncReport | None = None) -> None:
        try:
            ingested, skipped, failed, flags = await self._extract_risk([], self._page_size * 3)
            if target_report is not None and self._last_report is target_report:
                self._last_report = target_report.model_copy(
                    update={
                        "ingested_count": ingested,
                        "skipped_count": skipped,
                        "failed_count": failed,
                        "triggered_risk_flags": flags,
                    }
                )
        except Exception:
            logging.getLogger(__name__).exception("京东资讯风险积压处理失败")

    def list_articles(self, *, limit: int, channel: str | None = None, query: str | None = None) -> list[JDNewsArticle]:
        """读取已落库的资讯，供页面请求使用。"""
        normalized = query.strip() if query is not None else ""
        effective_query = None if not normalized or normalized == "黄金" else normalized
        return self._repository.list_articles(limit=limit, channel=channel, query=effective_query)

    async def sync_once(
        self, *, page_size: int | None = None, all_channels: bool = False, extract: bool = True
    ) -> NewsSyncReport:
        """拉取原文并提交，可显式等待本轮风险提取。"""
        size = page_size if page_size is not None else self._page_size
        async with self._lock:
            articles = await self._collect_channel("flash", size)
            if all_channels or not articles:
                articles.extend(await self._collect_channel("news", size))
            if all_channels:
                auth = await self._jd_gold.auth.auth_state_async()
                if auth.is_authorized():
                    articles.extend(await self._collect_channel("morning", size))
                else:
                    self._errors["morning"] = "AUTH_REQUIRED"

        ingested, skipped, failed, flags = await self._extract_risk(articles, size) if extract else (0, 0, 0, [])
        report = NewsSyncReport(
            fetched_count=len(articles),
            ingested_count=ingested,
            skipped_count=skipped,
            failed_count=failed,
            triggered_risk_flags=flags,
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
            "errors": self._errors.copy(),
            "last_report": self._last_report.model_dump(mode="json") if self._last_report is not None else None,
        }

    async def start(self) -> None:
        """启用后台轮询时采集原文，模型可独立关闭。"""
        if self._enabled and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._poll())

    async def stop(self) -> None:
        """停止后台快讯轮询任务。"""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._risk_task is not None:
            self._risk_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._risk_task
            self._risk_task = None
