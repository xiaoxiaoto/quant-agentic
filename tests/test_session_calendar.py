"""交易时段日历单元测试。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.services.market.session_calendar import SessionCalendar

_SH = ZoneInfo("Asia/Shanghai")


def _dt(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """辅助函数：创建北京时间带时区的 datetime。"""
    return datetime(year, month, day, hour, minute, tzinfo=_SH)


class TestIsTradingTime:
    """交易时段判断测试：周一 09:00 至周六 02:30（北京时间）。"""

    def test_monday_before_open(self) -> None:
        """周一 08:59 未开市，不可交易。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 1, 8, 59)
        assert dt.weekday() == 0  # 周一
        assert cal.is_trading_time(dt) is False

    def test_monday_at_open(self) -> None:
        """周一 09:00 正式开市，可交易。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 1, 9, 0)
        assert cal.is_trading_time(dt) is True

    def test_monday_afternoon(self) -> None:
        """周一下午交易进行中。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 1, 15, 0)
        assert cal.is_trading_time(dt) is True

    def test_tuesday_midnight(self) -> None:
        """周二 00:00 连续夜盘交易进行中。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 2, 0, 0)
        assert dt.weekday() == 1
        assert cal.is_trading_time(dt) is True

    def test_wednesday_all_day(self) -> None:
        """周三全天各时刻均为交易时段。"""
        cal = SessionCalendar()
        for hour in (0, 6, 12, 18, 23):
            dt = _dt(2024, 1, 3, hour)
            assert cal.is_trading_time(dt) is True

    def test_friday_late_night(self) -> None:
        """周五深夜 23:00 持续交易。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 5, 23, 0)
        assert dt.weekday() == 4
        assert cal.is_trading_time(dt) is True

    def test_saturday_before_close(self) -> None:
        """周六凌晨 02:00（闭市前 30 分钟）仍可交易。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 6, 2, 0)
        assert dt.weekday() == 5
        assert cal.is_trading_time(dt) is True

    def test_saturday_at_close(self) -> None:
        """周六凌晨 02:30 周交易闭市，不可交易。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 6, 2, 30)
        assert cal.is_trading_time(dt) is False

    def test_saturday_afternoon(self) -> None:
        """周六下午周末休市中。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 6, 15, 0)
        assert cal.is_trading_time(dt) is False

    def test_sunday(self) -> None:
        """周日全天休市。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 7, 12, 0)
        assert dt.weekday() == 6
        assert cal.is_trading_time(dt) is False


class TestHolidays:
    """节假日休市逻辑测试。"""

    def test_holiday_overrides_weekday(self) -> None:
        """登记节假日后，即使是周三也不可交易。"""
        cal = SessionCalendar(holidays=[date(2024, 1, 3)])
        dt = _dt(2024, 1, 3, 12, 0)
        assert dt.weekday() == 2
        assert cal.is_trading_time(dt) is False

    def test_add_holidays(self) -> None:
        """动态登记节假日生效。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 3, 12, 0)
        assert cal.is_trading_time(dt) is True
        cal.add_holidays([date(2024, 1, 3)])
        assert cal.is_trading_time(dt) is False


class TestWeekendRiskWindow:
    """周末风险窗口检测测试。"""

    def test_friday_evening_in_window(self) -> None:
        """周五夜间 23:00 距周六 02:30 仅剩 3.5 小时，处于 4 小时风控窗口内。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 5, 23, 0)
        assert cal.is_in_weekend_risk_window(dt, hours_before_close=4.0) is True

    def test_friday_morning_not_in_window(self) -> None:
        """周五上午 09:00 距闭市尚远，不在风控窗口期。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 5, 9, 0)
        assert cal.is_in_weekend_risk_window(dt, hours_before_close=4.0) is False

    def test_non_trading_returns_false(self) -> None:
        """非交易时段（如周日）返回 False。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 7, 12, 0)  # 周日
        assert cal.is_in_weekend_risk_window(dt) is False


class TestSessionTiming:
    """交易时长核算测试。"""

    def test_trading_hours_per_week(self) -> None:
        """周一 09:00 至周六 02:30 每周实际交易小时数为 113.5 小时。"""
        cal = SessionCalendar()
        assert cal.trading_hours_per_week() == pytest.approx(113.5)

    def test_weekend_gap_hours(self) -> None:
        """周末休市时长为 168 − 113.5 = 54.5 小时。"""
        cal = SessionCalendar()
        assert cal.weekend_gap_hours() == pytest.approx(54.5)

    def test_trading_hours_per_year(self) -> None:
        """全年交易小时数基准（113.5 × 52 ≈ 5902 小时）。"""
        cal = SessionCalendar()
        expected = 113.5 * 52
        assert cal.trading_hours_per_year() == pytest.approx(expected)

    def test_current_session_end_during_trading(self) -> None:
        """交易时段内查询，闭市时间为本周周六凌晨 02:30。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 3, 12, 0)  # 周三
        end = cal.current_session_end(dt)
        assert end is not None
        assert end.weekday() == 5  # 周六
        assert end.hour == 2
        assert end.minute == 30

    def test_current_session_end_not_trading(self) -> None:
        """非交易时段查询返回 None。"""
        cal = SessionCalendar()
        dt = _dt(2024, 1, 7, 12, 0)  # 周日
        assert cal.current_session_end(dt) is None


class TestTimezoneConversion:
    """跨时区输入自动换算测试。"""

    def test_utc_input(self) -> None:
        """UTC 周一 01:00 = 北京时间周一 09:00，属于开市交易时段。"""
        cal = SessionCalendar()
        utc = ZoneInfo("UTC")
        dt = datetime(2024, 1, 1, 1, 0, tzinfo=utc)
        assert cal.is_trading_time(dt) is True

    def test_utc_before_open(self) -> None:
        """UTC 周一 00:00 = 北京时间周一 08:00，尚未开市。"""
        cal = SessionCalendar()
        utc = ZoneInfo("UTC")
        dt = datetime(2024, 1, 1, 0, 0, tzinfo=utc)
        assert cal.is_trading_time(dt) is False


def test_trading_hours_excludes_weekends_and_holidays() -> None:
    cal = SessionCalendar(holidays=[date(2026, 3, 10)])
    assert cal.trading_hours_between(_dt(2026, 3, 7, 2, 30), _dt(2026, 3, 9, 9)) == 0
    assert cal.trading_hours_between(_dt(2026, 3, 9, 9), _dt(2026, 3, 11, 9)) == 24
    assert cal.trading_hours_between(_dt(2026, 3, 9, 9), _dt(2026, 3, 9, 10)) == 1
