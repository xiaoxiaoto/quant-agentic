"""交易时段日历服务。

连续交易时段：周一 09:00 至周六 02:30（北京时间）。
周末休市：约 54.5 小时（周六 02:30 至周一 09:00），遇节假日依公告调整。

「有效 K 线」仅指在银行交易时段内由实际报价生成的柱。
5 分钟仅为系统数据采集与监测节拍，不代表交易执行频率。
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.contracts.domains.product import SessionConfig


class SessionCalendar:
    """黄金积存交易时段日历。

    节假日需从银行/平台公告中提取并进行版本登记。
    时段未明确时采取安全原则（默认按不可交易处理）。
    """

    def __init__(
        self,
        config: SessionConfig | None = None,
        holidays: list[date] | None = None,
    ) -> None:
        cfg = config or SessionConfig()
        self._tz = ZoneInfo(cfg.timezone)
        self._open_day = cfg.week_open_day
        self._open_time = time(cfg.week_open_hour, cfg.week_open_minute)
        self._close_day = cfg.week_close_day
        self._close_time = time(cfg.week_close_hour, cfg.week_close_minute)
        self._holidays: set[date] = set(holidays or [])

    def _to_local(self, dt: datetime) -> datetime:
        return dt.astimezone(self._tz)

    def is_trading_time(self, dt: datetime) -> bool:
        """检查指定时间点 dt 是否处于连续交易时段内。

        节假日返回 False。常规周循环判断规则：
            周一：09:00 开始交易
            周二至周五：00:00 至 23:59（全天连续）
            周六：00:00 至 02:30（凌晨闭市）
            周六 02:30 后至周日全天：休市
        """
        local = self._to_local(dt)

        if local.date() in self._holidays:
            return False

        wd = local.weekday()  # 0=周一 … 6=周日
        t = local.time()

        if wd == self._open_day:
            return t >= self._open_time
        elif self._open_day < wd < self._close_day:
            return True
        elif wd == self._close_day:
            return t < self._close_time
        else:
            return False

    def is_in_weekend_risk_window(self, dt: datetime, hours_before_close: float = 4.0) -> bool:
        """检查 dt 是否处于周六 02:30 闭市前的风险窗口期内。

        用于实施周五收盘前禁开新仓、控制周末风险桶的保护规则。
        """
        if not self.is_trading_time(dt):
            return False
        close = self.current_session_end(dt)
        if close is None:
            return False
        delta = close - self._to_local(dt)
        return delta.total_seconds() <= hours_before_close * 3600

    def current_session_end(self, dt: datetime) -> datetime | None:
        """返回当前连续交易时段的闭市时间点（常规周六 02:30，遇周中节假日则提前至节假日 00:00）。

        若 dt 不处于交易时段内则返回 None。
        """
        if not self.is_trading_time(dt):
            return None
        local = self._to_local(dt)
        wd = local.weekday()
        days_until_close = (self._close_day - wd) % 7
        close_date = local.date() + timedelta(days=days_until_close)
        # 检查当前日期之后至常规闭市日前是否存在周中节假日
        probe = local.date() + timedelta(days=1)
        while probe <= close_date:
            if probe in self._holidays:
                return datetime.combine(probe, time.min, tzinfo=self._tz)
            probe += timedelta(days=1)
        return datetime.combine(close_date, self._close_time, tzinfo=self._tz)

    def next_session_start(self, dt: datetime) -> datetime:
        """返回 dt 之后的下一个非节假日开市时间（周一为常规开盘点，周二至周五顺延自 00:00）。"""
        local = self._to_local(dt)
        wd = local.weekday()
        days_until_monday = (self._open_day - wd) % 7
        if days_until_monday == 0 and local.time() >= self._open_time:
            days_until_monday = 7
        candidate = local.date() + timedelta(days=days_until_monday)
        # 若候选开市日为节假日，逐日顺延至下一个有效交易日
        while True:
            cand_wd = candidate.weekday()
            if candidate not in self._holidays and self._open_day <= cand_wd < self._close_day:
                open_t = self._open_time if cand_wd == self._open_day else time.min
                return datetime.combine(candidate, open_t, tzinfo=self._tz)
            candidate += timedelta(days=1)

    def trading_hours_between(self, start: datetime, end: datetime) -> float:
        """估算两时间点之间的交易小时数。"""
        if end <= start:
            return 0.0
        local_start, local_end = self._to_local(start), self._to_local(end)
        current_date = local_start.date()
        total_seconds = 0.0
        while current_date <= local_end.date():
            weekday = current_date.weekday()
            if current_date not in self._holidays and self._open_day <= weekday <= self._close_day:
                daily_start = datetime.combine(
                    current_date,
                    self._open_time if weekday == self._open_day else time.min,
                    tzinfo=self._tz,
                )
                daily_end = (
                    datetime.combine(current_date, self._close_time, tzinfo=self._tz)
                    if weekday == self._close_day
                    else datetime.combine(current_date + timedelta(days=1), time.min, tzinfo=self._tz)
                )
                overlap = min(local_end, daily_end) - max(local_start, daily_start)
                total_seconds += max(overlap.total_seconds(), 0.0)
            current_date += timedelta(days=1)
        return total_seconds / 3600.0

    def trading_hours_per_week(self) -> float:
        """每周标准交易小时数（周一 09:00 至周六 02:30 = 113.5 小时）。"""
        open_dt = datetime.combine(date(2024, 1, 1), self._open_time)
        close_dt = open_dt + timedelta(
            days=self._close_day - self._open_day,
        )
        close_dt = close_dt.replace(hour=self._close_time.hour, minute=self._close_time.minute)
        return (close_dt - open_dt).total_seconds() / 3600

    def trading_hours_per_year(self) -> float:
        """每年标准交易小时数估算（~5902 小时，113.5 × 52）。"""
        return self.trading_hours_per_week() * 52

    def weekend_gap_hours(self) -> float:
        """周末休市时长（~54.5 小时）。"""
        return 168.0 - self.trading_hours_per_week()

    def get_hours_until_weekend_close(self, dt: datetime) -> float:
        """获取距离当前交易周闭市（周六 02:30）的剩余小时数。"""
        close = self.current_session_end(dt)
        if close is None:
            return 0.0
        delta = close - self._to_local(dt)
        return max(delta.total_seconds() / 3600.0, 0.0)

    def add_holidays(self, dates: list[date]) -> None:
        """登记法定节假日或暂停交易日（不可交易）。"""
        self._holidays.update(dates)
