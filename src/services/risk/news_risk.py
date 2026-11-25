"""事件日历与新闻风险分析服务。

结合预定宏观事件日历与大模型新闻结构化提取。
输出事件锁定状态与风险标志，严格不直接产生下单建议。
"""

from datetime import UTC, datetime
from threading import RLock

from src.contracts.domains.event import MacroCalendarEvent, NewsEvent
from src.repositories.runtime_state import RuntimeStateRepository


class NewsRiskEngine:
    """宏观日程锁定与新闻事件风控引擎。"""

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
        if self._state_store is not None:
            self._macro_events = self._state_store.load_macro_events()
            for item in self._state_store.load_news_events():
                self._active_news_events[item.event_id] = item

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _is_event_active(self, event: MacroCalendarEvent, current_time: datetime) -> bool:
        diff_hours = (event.scheduled_at - current_time).total_seconds() / 3600.0
        # 在公布前 lock_window_hours_before 到公布后 lock_window_hours_after 之间为锁定期
        is_before_lock = 0.0 <= diff_hours <= event.lock_window_hours_before
        is_after_lock = -event.lock_window_hours_after <= diff_hours < 0.0
        return is_before_lock or is_after_lock

    def register_macro_event(self, event: MacroCalendarEvent) -> None:
        """注册预定宏观重大日程事件并持久化。"""
        with self._lock:
            self._macro_events = [item for item in self._macro_events if item.event_id != event.event_id]
            self._macro_events.append(event)
            if self._state_store is not None:
                self._state_store.upsert_macro_event(event)

    def is_event_locked(self, current_time: datetime | None = None) -> tuple[bool, str | None]:
        """检查当前时点是否处于重大宏观事件锁定禁入期。

        规则日历优先判定，不等大模型解析结果。
        """
        now_dt = current_time or self._now()
        with self._lock:
            events = tuple(self._macro_events)
        for event in events:
            if self._is_event_active(event, now_dt):
                return True, f"处于重大日程锁定窗口：{event.event_name}，公布时间 {event.scheduled_at}"
        return False, None

    def has_news_event(self, event_id: str) -> bool:
        """检查指定新闻事件标识是否已录入风险引擎（用于定时同步前置去重）。"""
        with self._lock:
            return event_id in self._active_news_events

    def has_duplicate_news(self, title: str, content: str) -> bool:
        """按归一化标题与正文检查是否已存在相同内容的资讯（防止上游更换 ID 导致重复入库）。"""
        norm_title = title.strip()
        norm_content = content.strip()
        with self._lock:
            return any(
                item.title.strip() == norm_title and item.content.strip() == norm_content
                for item in self._active_news_events.values()
            )

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
            self._active_news_events[stored.event_id] = stored
            if self._state_store is not None:
                self._state_store.upsert_news_event(stored)
            return list(stored.risk_flags)

    def get_active_risk_flags(self, current_time: datetime | None = None) -> list[str]:
        """获取当前生效的所有宏观与新闻风控标志列表。"""
        flags: list[str] = []
        locked, reason = self.is_event_locked(current_time)
        if locked and reason:
            flags.append("EVENT_LOCK")

        now_dt = current_time or self._now()
        with self._lock:
            events = tuple(self._active_news_events.values())
        # 仅保留最近 24 小时内且满足阈值的新闻标志
        for event in events:
            if event.available_at is not None and event.available_at > now_dt:
                continue
            age_hours = (now_dt - event.published_at).total_seconds() / 3600.0
            if 0.0 <= age_hours <= 24.0:
                if event.severity >= self._severity_th and event.confidence >= self._confidence_th:
                    flags.extend(event.risk_flags)

        return sorted(set(flags))
