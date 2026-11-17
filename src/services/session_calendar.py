"""交易时段日历服务。

连续交易时段：周一 09:00 至周六 02:30（北京时间）。
周末休市：约 54.5 小时（周六 02:30 至周一 09:00），遇节假日依公告调整。

「有效 K 线」仅指在银行交易时段内由实际报价生成的柱。
5 分钟仅为系统数据采集与监测节拍，不代表交易执行频率。
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.types.domain.product import SessionConfig

_DEFAULT_TZ = ZoneInfo("Asia/Shanghai")


class SessionCalendar:
    """黄金积存交易时段日历。

    节假日需从银行/平台公告中提取并进行版本登记。
    时段未明确时采取安全原则（默认按不可交易处理）。
    """

    _WEEKEND_GAP_HOURS: float = 54.5

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
        """返回当前交易周的闭市时间点（周六 02:30）。

        若 dt 不处于交易时段内则返回 None。
        """
        if not self.is_trading_time(dt):
            return None
        local = self._to_local(dt)
        wd = local.weekday()
        days_until_close = (self._close_day - wd) % 7
        close_date = local.date() + timedelta(days=days_until_close)
        return datetime.combine(close_date, self._close_time, tzinfo=self._tz)

    def next_session_start(self, dt: datetime) -> datetime:
        """返回 dt 之后（或含当前非交易时段）的下一个周一 09:00 开市时间。"""
        local = self._to_local(dt)
        wd = local.weekday()
        days_until_monday = (self._open_day - wd) % 7
        if days_until_monday == 0 and local.time() >= self._open_time:
            days_until_monday = 7
        candidate = local.date() + timedelta(days=days_until_monday)
        result = datetime.combine(candidate, self._open_time, tzinfo=self._tz)
        # 遇节假日自动顺延至下一开市日
        while result.date() in self._holidays:
            result += timedelta(days=7)
        return result

    def trading_hours_between(self, start: datetime, end: datetime) -> float:
        """估算两时间点之间的交易小时数。"""
        total_hours = (end - start).total_seconds() / 3600
        full_weeks = total_hours / 168.0  # 7 × 24
        return full_weeks * self.trading_hours_per_week()

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

    def add_holidays(self, dates: list[date]) -> None:
        """登记法定节假日或暂停交易日（不可交易）。"""
        self._holidays.update(dates)
