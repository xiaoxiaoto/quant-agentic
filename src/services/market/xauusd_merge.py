"""按整根 OHLC 合并三路现货黄金行情。"""

from datetime import UTC, date, datetime, time, timedelta
from typing import ClassVar
from zoneinfo import ZoneInfo

from src.contracts.domains.market_bar import XAUUSD_MERGED_SOURCE, XAUUSD_PRODUCT, KLineBar, XauusdOrigin
from src.repositories.market_bar import MarketBarRepository


class XauusdBarMerge:
    """生成带原始来源的现货黄金日线及分钟线。"""

    _NEW_YORK: ClassVar[ZoneInfo] = ZoneInfo("America/New_York")
    _BUY_SOURCES: ClassVar[tuple[XauusdOrigin, ...]] = ("dukascopy_bid", "histdata_bid")
    _SOURCE_PRIORITY: ClassVar[dict[XauusdOrigin, int]] = {
        "dukascopy_bid": 3,
        "histdata_bid": 2,
        "jd_public_kline": 1,
    }
    _MINUTES: ClassVar[dict[str, int]] = {"m1": 1, "m5": 5, "m15": 15, "m30": 30, "m60": 60}
    _HOUR: ClassVar[timedelta] = timedelta(hours=1)
    _INTRADAY_WINDOW_DAYS: ClassVar[int] = 31

    def __init__(self, repository: MarketBarRepository) -> None:
        self._repository = repository

    @staticmethod
    def _to_utc(dt: datetime) -> datetime:
        return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    @staticmethod
    def _valid(bar: KLineBar) -> bool:
        body_low = min(bar.open_price, bar.close_price)
        body_high = max(bar.open_price, bar.close_price)
        return bar.low_price <= body_low <= body_high <= bar.high_price

    @classmethod
    def _session_close(cls, trade_date: date) -> datetime:
        return datetime.combine(trade_date, time(17), cls._NEW_YORK).astimezone(UTC)

    @staticmethod
    def _tag(bar: KLineBar, source: XauusdOrigin, **updates: object) -> KLineBar:
        return bar.model_copy(update={"source": XAUUSD_MERGED_SOURCE, "origin_source": source, **updates})

    def _latest_before(self, cutoff: datetime) -> datetime | None:
        """探测早于 cutoff 的最近一条原始 K 线时间，用于分窗回溯时跳过空窗或提前终止。"""
        probe_end = cutoff - timedelta(microseconds=1)
        latest: datetime | None = None
        for source, interval in (
            ("dukascopy_bid", "m60"),
            ("histdata_bid", "m60"),
            ("jd_public_kline", "day"),
        ):
            rows = self._repository.query_klines(XAUUSD_PRODUCT, interval, source=source, end_time=probe_end, limit=1)
            if rows:
                stamp = rows[0].bar_time.astimezone(UTC)
                latest = stamp if latest is None or stamp > latest else latest
        return latest

    def _latest_intraday_before(self, interval: str, cutoff: datetime) -> datetime | None:
        """探测早于 cutoff 的最近一条分钟/小时原始 K 线时间，用于分窗导出跳过空窗。"""
        probe_end = cutoff - timedelta(microseconds=1)
        latest: datetime | None = None
        for source in self._BUY_SOURCES:
            rows = self._repository.query_klines(XAUUSD_PRODUCT, interval, source=source, end_time=probe_end, limit=1)
            if rows:
                stamp = rows[0].bar_time.astimezone(UTC)
                latest = stamp if latest is None or stamp > latest else latest
        return latest

    def _can_replace(self, existing: KLineBar | None, source: XauusdOrigin) -> bool:
        if existing is None or existing.origin_source is None:
            return True
        return self._SOURCE_PRIORITY[source] > self._SOURCE_PRIORITY[existing.origin_source]

    def _collect_daily_window(
        self,
        hour_start: datetime | None,
        hour_end: datetime,
        now: datetime,
        by_date: dict[date, KLineBar],
    ) -> None:
        """收集单个时间窗内各来源可合成的日线并按优先级写入 by_date。"""
        for source in self._BUY_SOURCES:
            hours = self._repository.query_klines(
                XAUUSD_PRODUCT, "m60", source=source, start_time=hour_start, end_time=hour_end, limit=None
            )
            sessions: dict[date, dict[datetime, KLineBar]] = {}
            for bar in hours:
                stamp = bar.bar_time.astimezone(UTC)
                session = self.session_date(stamp)
                if session is not None and self._can_replace(by_date.get(session), source) and self._valid(bar):
                    sessions.setdefault(session, {})[stamp] = bar
            for session, session_hours in sessions.items():
                close = self._session_close(session)
                expected = [close - step * self._HOUR for step in range(23, 0, -1)]
                if close > now or not all(stamp in session_hours for stamp in expected):
                    continue
                bars = [session_hours[stamp] for stamp in expected]
                available_at = max(close, *(bar.available_at or close for bar in bars))
                if available_at > now:
                    continue
                volumes = [bar.volume for bar in bars if bar.volume is not None]
                by_date[session] = self._tag(
                    bars[0],
                    source,
                    interval="day",
                    trade_date=session.isoformat(),
                    bar_time=datetime.combine(session, time.min, UTC),
                    available_at=available_at,
                    high_price=max(bar.high_price for bar in bars),
                    low_price=min(bar.low_price for bar in bars),
                    close_price=bars[-1].close_price,
                    collected_at=max(bar.collected_at for bar in bars),
                    pre_close=None,
                    volume=sum(volumes) if len(volumes) == len(bars) else None,
                    turnover=None,
                )

        for bar in self._repository.query_klines(
            XAUUSD_PRODUCT, "day", source="jd_public_kline", start_time=hour_start, end_time=hour_end, limit=None
        ):
            try:
                session = date.fromisoformat(bar.trade_date)
            except ValueError:
                continue
            if session in by_date or session.weekday() >= 5 or not self._valid(bar):
                continue
            close = self._session_close(session)
            # 左边界交易日的首小时早于当前窗口时交由更早窗口统一按 23 小时合成，避免低优先级源抢占。
            if hour_start is not None and close - 23 * self._HOUR < hour_start:
                continue
            available_at = max(close, bar.available_at or close)
            if close <= bar.collected_at and available_at <= now:
                by_date[session] = self._tag(
                    bar, "jd_public_kline", bar_time=datetime.combine(session, time.min, UTC), available_at=available_at
                )

    def _intraday_windowed(
        self,
        interval: str,
        start: datetime | None,
        end: datetime,
        now: datetime,
    ) -> list[KLineBar]:
        """全量导出路径按月分窗合并分钟/小时 K 线，避免双源无上限全表加载。"""
        candidates: dict[datetime, KLineBar] = {}
        delta = timedelta(minutes=self._MINUTES[interval])
        cursor_end = end
        window_span = timedelta(days=self._INTRADAY_WINDOW_DAYS)
        while True:
            window_start = cursor_end - window_span
            if start is not None and window_start < start:
                window_start = start
            for source in self._BUY_SOURCES:
                bars = self._repository.query_klines(
                    XAUUSD_PRODUCT,
                    interval,
                    source=source,
                    start_time=window_start,
                    end_time=cursor_end,
                    limit=None,
                )
                for bar in bars:
                    stamp = bar.bar_time.astimezone(UTC)
                    if stamp in candidates or not self._valid(bar) or self.session_date(stamp) is None:
                        continue
                    available_at = bar.available_at or stamp + delta
                    if available_at <= now:
                        candidates[stamp] = self._tag(bar, source, bar_time=stamp, available_at=available_at)
            if start is not None and window_start <= start:
                break
            older = self._latest_intraday_before(interval, window_start)
            if older is None or (start is not None and older < start):
                break
            next_end = min(window_start - timedelta(microseconds=1), older)
            if next_end >= cursor_end:
                break
            cursor_end = next_end
        return [candidates[stamp] for stamp in sorted(candidates)]

    def _intraday(
        self,
        interval: str,
        start: datetime | None,
        end: datetime | None,
        limit: int | None,
        now: datetime,
    ) -> list[KLineBar]:
        latest = now - timedelta(minutes=self._MINUTES[interval])
        end = min(end, latest) if end else latest
        if start is not None and start > end:
            return []
        if limit is None:
            return self._intraday_windowed(interval, start, end, now)
        candidates: dict[datetime, KLineBar] = {}
        batch_limit = max(limit + 64, limit + limit // 4)
        for source in self._BUY_SOURCES:
            cursor_end = end
            seen_valid = 0
            while True:
                bars = self._repository.query_klines(
                    XAUUSD_PRODUCT, interval, source=source, start_time=start, end_time=cursor_end, limit=batch_limit
                )
                if not bars:
                    break
                for bar in reversed(bars):
                    stamp = bar.bar_time.astimezone(UTC)
                    if not self._valid(bar) or self.session_date(stamp) is None:
                        continue
                    available_at = bar.available_at or stamp + timedelta(minutes=self._MINUTES[interval])
                    if available_at > now:
                        continue
                    seen_valid += 1
                    if stamp not in candidates:
                        candidates[stamp] = self._tag(bar, source, bar_time=stamp, available_at=available_at)
                if seen_valid >= limit or len(bars) < batch_limit:
                    break
                oldest = bars[0].bar_time.astimezone(UTC)
                next_end = oldest - timedelta(microseconds=1)
                if start is not None and next_end < start:
                    break
                cursor_end = next_end
        stamps = sorted(candidates)[-limit:]
        return [candidates[stamp] for stamp in stamps]

    def _daily(self, start: datetime | None, end: datetime | None, limit: int | None, now: datetime) -> list[KLineBar]:
        by_date: dict[date, KLineBar] = {}
        cursor_end = min(end, now) if end else now
        window_days = max(limit * 2 + 21, 60) if limit is not None else 90
        while True:
            window_start = cursor_end - timedelta(days=window_days)
            if start is not None and window_start < start:
                window_start = start
            lower = window_start.astimezone(UTC).date() - timedelta(days=1)
            upper = cursor_end.astimezone(UTC).date() + timedelta(days=2)
            hour_start = datetime.combine(lower, time.min, UTC)
            hour_end = min(now, datetime.combine(upper, time.min, UTC))
            self._collect_daily_window(hour_start, hour_end, now, by_date)
            if limit is not None:
                selected_count = sum(
                    1
                    for bar in by_date.values()
                    if (start is None or bar.bar_time >= start) and (end is None or bar.bar_time <= end)
                )
                if selected_count >= limit:
                    break
            if start is not None and window_start <= start:
                break
            older = self._latest_before(hour_start)
            if older is None or (start is not None and older < start - timedelta(days=1)):
                break
            next_end = min(window_start, older + timedelta(days=1))
            if next_end >= cursor_end:
                break
            cursor_end = next_end

        bars = (by_date[session] for session in sorted(by_date))
        selected = [
            bar for bar in bars if (start is None or bar.bar_time >= start) and (end is None or bar.bar_time <= end)
        ]
        return selected if limit is None else selected[-limit:]

    @classmethod
    def session_date(cls, stamp: datetime) -> date | None:
        """返回给定时刻所属纽约现货黄金交易日，休市与每日维护小时返回 None。"""
        local = stamp.astimezone(cls._NEW_YORK)
        if local.hour == 17:
            return None
        trade_date = local.date() + timedelta(days=local.hour >= 18)
        return trade_date if trade_date.weekday() < 5 else None

    def query(
        self,
        interval: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int | None = 500,
    ) -> list[KLineBar]:
        """按周期与时间范围返回合并行情及原始来源。"""
        if limit is not None:
            if limit < 0:
                raise ValueError("limit 不能为负数")
            if limit == 0:
                return []
        start_utc = self._to_utc(start_time) if start_time is not None else None
        end_utc = self._to_utc(end_time) if end_time is not None else None
        if start_utc is not None and end_utc is not None and start_utc > end_utc:
            return []
        now = datetime.now(UTC)
        if interval == "day":
            return self._daily(start_utc, end_utc, limit, now)
        if interval in self._MINUTES:
            return self._intraday(interval, start_utc, end_utc, limit, now)
        raise ValueError("XAUUSD 合并行情仅支持 m1、m5、m15、m30、m60、day")
