"""事件日历与新闻风险分析服务。

结合预定宏观事件日历与大模型新闻结构化提取。
输出事件锁定状态与风险标志，严格不直接产生下单建议。
"""

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import ClassVar

from src.contracts.domains.event import MacroCalendarEvent, NewsEvent
from src.repositories.runtime_state import RuntimeStateRepository


class NewsRiskEngine:
    """宏观日程锁定与新闻事件风控引擎。"""

    _MAX_CACHED_NEWS_EVENTS: ClassVar[int] = 2000

    def __init__(
        self,
        severity_threshold: float = 0.7,
        confidence_threshold: float = 0.6,
        state_store: RuntimeStateRepository | None = None,
    ) -> None:
        self._lock = RLock()
        self._state_store = state_store
        self._severity_th: float = severity_threshold
        self._confidence_th: float = confidence_threshold
        self._macro_events: list[MacroCalendarEvent] = []
        self._active_news_events: dict[str, NewsEvent] = {}
        self._content_keys: set[tuple[str, str]] = set()
        self._high_risk_times: list[datetime] = []
        self._high_risk_items: list[NewsEvent] = []
        if self._state_store is not None:
            self._macro_events = self._state_store.load_macro_events()
            for item in self._state_store.load_news_events(limit=self._MAX_CACHED_NEWS_EVENTS):
                self._index_news_event(item)

    @staticmethod
    def _to_utc(dt: datetime) -> datetime:
        return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    def _prune_news_cache(self) -> None:
        """按发布时间淘汰超出容量上限的旧新闻缓存，防止长期运行内存无限增长。"""
        if len(self._active_news_events) <= self._MAX_CACHED_NEWS_EVENTS:
            return
        ordered = sorted(
            self._active_news_events.values(),
            key=lambda e: self._to_utc(e.published_at),
            reverse=True,
        )
        kept = ordered[: self._MAX_CACHED_NEWS_EVENTS]
        self._active_news_events = {item.event_id: item for item in kept}
        self._content_keys = {(item.title.strip(), item.content.strip()) for item in kept}
        kept_ids = set(self._active_news_events)
        filtered_pairs = [
            (ts, item)
            for ts, item in zip(self._high_risk_times, self._high_risk_items, strict=True)
            if item.event_id in kept_ids
        ]
        self._high_risk_times = [ts for ts, _ in filtered_pairs]
        self._high_risk_items = [item for _, item in filtered_pairs]

    def _index_news_event(self, item: NewsEvent) -> None:
        """更新内存事件表、O(1) 内容去重集合以及按发布时间排序的高危索引。"""
        self._active_news_events[item.event_id] = item
        self._content_keys.add((item.title.strip(), item.content.strip()))
        if item.severity >= self._severity_th and item.confidence >= self._confidence_th and item.risk_flags:
            pub_utc = self._to_utc(item.published_at)
            pos = bisect_right(self._high_risk_times, pub_utc)
            self._high_risk_times.insert(pos, pub_utc)
            self._high_risk_items.insert(pos, item)
        self._prune_news_cache()

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _is_event_active(self, event: MacroCalendarEvent, current_time: datetime) -> bool:
        diff_hours = (self._to_utc(event.scheduled_at) - self._to_utc(current_time)).total_seconds() / 3600.0
        # 在公布前 lock_window_hours_before 到公布后 lock_window_hours_after 之间为锁定期
        is_before_lock = 0.0 <= diff_hours <= event.lock_window_hours_before
        is_after_lock = -event.lock_window_hours_after <= diff_hours < 0.0
        return is_before_lock or is_after_lock

    def _event_lock_status(self, events: Iterable[MacroCalendarEvent], now: datetime) -> tuple[bool, str | None]:
        for event in events:
            if self._is_event_active(event, now):
                return True, f"处于重大日程锁定窗口：{event.event_name}，公布时间 {event.scheduled_at}"
        return False, None

    def _active_flags(self, events: Iterable[NewsEvent], now: datetime, locked: bool) -> list[str]:
        now_utc = self._to_utc(now)
        flags = {"EVENT_LOCK"} if locked else set()
        for event in events:
            if event.available_at is not None and self._to_utc(event.available_at) > now_utc:
                continue
            age_hours = (now_utc - self._to_utc(event.published_at)).total_seconds() / 3600.0
            if (
                0.0 <= age_hours <= 24.0
                and event.severity >= self._severity_th
                and event.confidence >= self._confidence_th
            ):
                flags.update(event.risk_flags)
        return sorted(flags)

    def register_macro_event(self, event: MacroCalendarEvent) -> None:
        """注册预定宏观重大日程事件并持久化。"""
        with self._lock:
            if self._state_store is not None:
                self._state_store.upsert_macro_event(event)
            self._macro_events = [item for item in self._macro_events if item.event_id != event.event_id]
            self._macro_events.append(event)

    def is_event_locked(self, current_time: datetime | None = None) -> tuple[bool, str | None]:
        """检查当前时点是否处于重大宏观事件锁定禁入期。

        规则日历优先判定，不等大模型解析结果。
        """
        now_dt = current_time or self._now()
        with self._lock:
            events = tuple(self._macro_events)
        return self._event_lock_status(events, now_dt)

    def has_news_event(self, event_id: str) -> bool:
        """检查指定新闻事件标识是否已录入风险引擎（用于定时同步前置去重）。"""
        with self._lock:
            return event_id in self._active_news_events

    def has_duplicate_news(self, title: str, content: str) -> bool:
        """按归一化标题与正文检查是否已存在相同内容的资讯（防止上游更换 ID 导致重复入库）。"""
        key = (title.strip(), content.strip())
        with self._lock:
            return key in self._content_keys

    def list_news_events(self, limit: int = 50) -> list[NewsEvent]:
        """按发布时间倒序返回已录入的新闻风险事件列表。"""
        with self._lock:
            ordered = sorted(self._active_news_events.values(), key=lambda e: e.published_at, reverse=True)
        return [item.model_copy(deep=True) for item in ordered[: max(1, limit)]]

    def list_macro_events(self) -> list[MacroCalendarEvent]:
        """按预定公布时间升序返回已注册的宏观重大日程事件。"""
        with self._lock:
            ordered = sorted(self._macro_events, key=lambda e: e.scheduled_at)
        return [item.model_copy(deep=True) for item in ordered]

    def ingest_news(self, event: NewsEvent) -> list[str]:
        """录入并解析新闻事件，生成高危风控标志。"""
        with self._lock:
            if event.event_id in self._active_news_events:
                existing = self._active_news_events[event.event_id]
                excluded = {"available_at", "risk_flags"}
                if existing.model_dump(exclude=excluded) != event.model_dump(exclude=excluded):
                    raise ValueError("新闻事件标识已存在且内容冲突，请为修订新闻使用新标识")
                return list(existing.risk_flags)
            stored = event.model_copy(deep=True)
            stored.risk_flags = []
            if stored.severity >= self._severity_th and stored.confidence >= self._confidence_th:
                stored.risk_flags.append(f"NEWS_RISK_{stored.event_type}_{stored.event_id}")
            if self._state_store is not None:
                self._state_store.upsert_news_event(stored)
            self._index_news_event(stored)
            return list(stored.risk_flags)

    def get_active_risk_flags(self, current_time: datetime | None = None) -> list[str]:
        """获取当前生效的所有宏观与新闻风控标志列表。"""
        now = current_time or self._now()
        now_utc = self._to_utc(now)
        window_start = now_utc - timedelta(hours=24)
        with self._lock:
            locked, _ = self._event_lock_status(self._macro_events, now)
            start_idx = bisect_left(self._high_risk_times, window_start)
            end_idx = bisect_right(self._high_risk_times, now_utc)
            candidates = self._high_risk_items[start_idx:end_idx]
            return self._active_flags(candidates, now, locked)

    def stored_overview(
        self, now: datetime, limit: int
    ) -> tuple[bool, str | None, list[str], list[NewsEvent], list[MacroCalendarEvent]]:
        """读取已提交的资讯与日程，并在查询时计算当前风险。"""
        if self._state_store is None:
            locked, reason = self.is_event_locked(now)
            return (
                locked,
                reason,
                self.get_active_risk_flags(now),
                self.list_news_events(limit),
                self.list_macro_events(),
            )
        macro = self._state_store.load_macro_events()
        latest = self._state_store.load_news_events(limit=limit)
        recent = self._state_store.load_news_events(published_since=now - timedelta(hours=24))
        locked, reason = self._event_lock_status(macro, now)
        return locked, reason, self._active_flags(recent, now, locked), latest, macro
