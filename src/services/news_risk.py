"""事件日历与新闻风险分析服务。

结合预定宏观事件日历与大模型新闻结构化提取。
输出事件锁定状态与风险标志，严格不直接产生下单建议。
"""

from datetime import UTC, datetime

from src.types.domain.event import MacroCalendarEvent, NewsEvent


class NewsRiskEngine:
    """宏观日程锁定与新闻事件风控引擎。"""

    def __init__(
        self,
        severity_threshold: float = 0.7,
        confidence_threshold: float = 0.6,
    ) -> None:
        self._severity_th: float = severity_threshold
        self._confidence_th: float = confidence_threshold
        self._macro_events: list[MacroCalendarEvent] = []
        self._active_news_events: list[NewsEvent] = []

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _is_event_active(self, event: MacroCalendarEvent, current_time: datetime) -> bool:
        diff_hours = (event.scheduled_at - current_time).total_seconds() / 3600.0
        # 在公布前 lock_window_hours_before 到公布后 lock_window_hours_after 之间为锁定期
        is_before_lock = 0.0 <= diff_hours <= event.lock_window_hours_before
        is_after_lock = -event.lock_window_hours_after <= diff_hours < 0.0
        return is_before_lock or is_after_lock

    def register_macro_event(self, event: MacroCalendarEvent) -> None:
        """注册预定宏观重大日程事件。"""
        self._macro_events.append(event)

    def is_event_locked(self, current_time: datetime | None = None) -> tuple[bool, str | None]:
        """检查当前时点是否处于重大宏观事件锁定禁入期。

        规则日历优先判定，不等大模型解析结果。
        """
        now_dt = current_time or self._now()
        for event in self._macro_events:
            if self._is_event_active(event, now_dt):
                return True, f"处于重大日程锁定窗口：{event.event_name}，公布时间 {event.scheduled_at}"
        return False, None

    def ingest_news(self, event: NewsEvent) -> list[str]:
        """录入并解析新闻事件，生成高危风控标志。"""
        self._active_news_events.append(event)
        new_flags: list[str] = []

        if event.severity >= self._severity_th and event.confidence >= self._confidence_th:
            flag = f"NEWS_RISK_{event.event_type}_{event.event_id}"
            new_flags.append(flag)
            event.risk_flags.append(flag)

        return new_flags

    def get_active_risk_flags(self, current_time: datetime | None = None) -> list[str]:
        """获取当前生效的所有宏观与新闻风控标志列表。"""
        flags: list[str] = []
        locked, reason = self.is_event_locked(current_time)
        if locked and reason:
            flags.append("EVENT_LOCK")

        now_dt = current_time or self._now()
        # 仅保留最近 24 小时内且满足阈值的新闻标志
        for event in self._active_news_events:
            age_hours = (now_dt - event.published_at).total_seconds() / 3600.0
            if 0.0 <= age_hours <= 24.0:
                if event.severity >= self._severity_th and event.confidence >= self._confidence_th:
                    flags.extend(event.risk_flags)

        return sorted(set(flags))
