"""外部黄金 K 线增量回填与小时聚合。"""

from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from itertools import groupby
from typing import ClassVar, Literal
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from src.clients.external_bars import ExternalBarClient
from src.clients.macro_bars import MacroBarClient
from src.contracts.domains.market_bar import (
    MACRO_DAILY_PRODUCTS,
    ExternalBarResult,
    ExternalBarSource,
    ExternalCandle,
    ExternalKLineSource,
    KLineBar,
)
from src.repositories.market_bar import MarketBarRepository
from src.services.market.xauusd_merge import XauusdBarMerge


class ExternalBarSync:
    """逐批写入独立数据序列，保留已提交的历史区间。"""

    _SHANGHAI: ClassVar[ZoneInfo] = ZoneInfo("Asia/Shanghai")

    def __init__(
        self,
        repository: MarketBarRepository,
        client: ExternalBarClient,
        *,
        macro_client: MacroBarClient | None = None,
    ) -> None:
        self._repository = repository
        self._client = client
        self._macro_client = macro_client
        self._now = datetime.now(UTC)

    @staticmethod
    def _months(start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
        while start < end:
            year = start.year + (start.month == 12)
            next_month = datetime(year, start.month % 12 + 1, 1, tzinfo=UTC)
            stop = min(end, next_month)
            yield start, stop
            start = stop

    @staticmethod
    def _aggregate(bars: list[KLineBar], minutes: int) -> Iterator[KLineBar]:
        buckets: dict[datetime, list[KLineBar]] = {}
        for bar in bars:
            key = bar.bar_time.replace(minute=bar.bar_time.minute // minutes * minutes)
            buckets.setdefault(key, []).append(bar)
        for stamp, bucket in buckets.items():
            if len(bucket) != minutes:
                continue
            first, last = bucket[0], bucket[-1]
            volumes = [bar.volume for bar in bucket if bar.volume is not None]
            yield first.model_copy(
                update={
                    "interval": f"m{minutes}",
                    "bar_time": stamp,
                    "available_at": last.available_at,
                    "high_price": max(bar.high_price for bar in bucket),
                    "low_price": min(bar.low_price for bar in bucket),
                    "close_price": last.close_price,
                    "volume": sum(volumes) if len(volumes) == minutes else None,
                }
            )

    def _start(
        self,
        product: str,
        interval: str,
        source: ExternalKLineSource,
        explicit_start: date | None,
        default_start: date,
    ) -> datetime:
        zone = (
            self._SHANGHAI
            if source == "sge_daily"
            else (self._client.histdata_tz() if source == "histdata_bid" else UTC)
        )
        if explicit_start is not None:
            return datetime.combine(explicit_start, time.min, zone).astimezone(UTC)
        latest = self._repository.query_klines(product, interval, source=source, limit=1)
        if latest:
            previous = latest[0].bar_time.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
            return previous - timedelta(hours=1)
        return datetime.combine(default_start, time.min, zone).astimezone(UTC)

    def _save(
        self,
        rows: Iterable[ExternalCandle],
        *,
        product: str,
        source: ExternalKLineSource,
        interval: str,
        start: datetime,
        end: datetime,
        result: ExternalBarResult,
        aggregate: bool = False,
    ) -> None:
        latest: dict[datetime, KLineBar] = {}
        is_dukascopy = source in ("dukascopy_bid", "dukascopy_ask")
        for row in rows:
            if row.time < start or row.time >= end or row.end > self._now:
                continue
            row_time_utc = row.time.astimezone(UTC)
            if is_dukascopy and XauusdBarMerge.session_date(row_time_utc) is None:
                continue
            if (
                row.end <= row.time
                or (interval == "m1" and (row.time.second or row.time.microsecond))
                or not row.low <= min(row.open, row.close) <= max(row.open, row.close) <= row.high
            ):
                result.rejected += 1
                continue
            try:
                bar = KLineBar(
                    product_code=product,
                    source=source,
                    interval=interval,
                    bar_time=row_time_utc,
                    trade_date=(
                        row.end.astimezone(self._SHANGHAI).date().isoformat()
                        if product == "SGE-Au99.99"
                        else row_time_utc.date().isoformat()
                    ),
                    open_price=row.open,
                    high_price=row.high,
                    low_price=row.low,
                    close_price=row.close,
                    volume=row.volume,
                    turnover=row.turnover,
                    available_at=row.end.astimezone(UTC),
                    collected_at=self._now,
                )
            except ValidationError:
                result.rejected += 1
                continue
            latest[bar.bar_time] = bar
        bars = sorted(latest.values(), key=lambda bar: bar.bar_time)
        if not aggregate:
            saved = self._repository.save_klines_with_counts({interval: bars})
            for period, count in saved.items():
                result.written[period] = result.written.get(period, 0) + count
            return
        # 按 UTC 交易日分段提交分钟柱及上层聚合周期，缩短单次 SQLite 写锁持有时间并保证水位与聚合一致。
        for _, day_iter in groupby(bars, key=lambda bar: bar.bar_time.date()):
            day_bars = list(day_iter)
            series = {interval: day_bars}
            series.update({f"m{minutes}": list(self._aggregate(day_bars, minutes)) for minutes in (5, 15, 30, 60)})
            saved = self._repository.save_klines_with_counts(series)
            for period, count in saved.items():
                result.written[period] = result.written.get(period, 0) + count

    def _sync_histdata(self, lower: datetime, upper: datetime, result: ExternalBarResult) -> None:
        zone = self._client.histdata_tz()
        current_year = self._now.astimezone(zone).year
        while lower < upper:
            local = lower.astimezone(zone)
            annual = local.year < current_year
            year = local.year + (annual or local.month == 12)
            month = 1 if annual else local.month % 12 + 1
            stop = min(upper, datetime(year, month, 1, tzinfo=zone).astimezone(UTC))
            rows = self._client.histdata(local.year, None if annual else local.month)
            # 归档以纽约固定时区划分，同一个 UTC 小时只会落入一个批次。
            for _, batch in groupby(rows, key=lambda row: (row.time.year, row.time.month)):
                self._save(
                    batch,
                    product="WG-XAUUSD",
                    source="histdata_bid",
                    interval="m1",
                    start=lower,
                    end=stop,
                    result=result,
                    aggregate=True,
                )
            lower = stop

    def _sync_sequence(
        self,
        source: ExternalKLineSource,
        interval: str,
        start: date | None,
        end: date | None,
        result: ExternalBarResult,
    ) -> None:
        is_sge = source == "sge_daily"
        product = "SGE-Au99.99" if is_sge else "WG-XAUUSD"
        default_start = date(2016, 12, 18) if is_sge else date(2024, 1, 1)
        lower = self._start(product, interval, source, start, default_start)
        # 上金所日线以北京时间、HistData 以纽约固定时区界定起止区间，统一转换到 UTC 后再判断区间是否有效。
        upper_zone = self._SHANGHAI if is_sge else (self._client.histdata_tz() if source == "histdata_bid" else UTC)
        upper = datetime.combine(end, time.min, upper_zone).astimezone(UTC) if end else self._now
        upper = min(upper, self._now)
        if source == "histdata_bid":
            upper = min(upper, self._client.histdata_upper_bound(self._now))
        if lower >= upper:
            return

        if is_sge:
            self._save(
                self._client.sge(),
                product=product,
                source=source,
                interval=interval,
                start=lower,
                end=upper,
                result=result,
            )
            return
        if source == "histdata_bid":
            self._sync_histdata(lower, upper, result)
            return
        side: Literal["bid", "ask"] = "ask" if source == "dukascopy_ask" else "bid"
        for left, right in self._months(lower, upper):
            for seg_start, seg_end in self._client.dukascopy_segments(left, right):
                self._save(
                    self._client.dukascopy(side, seg_start, seg_end),
                    product=product,
                    source=source,
                    interval=interval,
                    start=seg_start,
                    end=seg_end,
                    result=result,
                    aggregate=True,
                )

    def _sync_macro_product(
        self,
        product: str,
        start: date | None,
        end: date | None,
        result: ExternalBarResult,
    ) -> None:
        if self._macro_client is None:
            raise ValueError("未配置宏观日线客户端（MacroBarClient），无法同步宏观序列")
        default_start = date(2016, 12, 18)
        lower = self._start(product, "day", "macro_daily", start, default_start)
        upper = datetime.combine(end, time.min, UTC) if end else self._now
        upper = min(upper, self._now)
        if lower >= upper:
            return
        rows = self._macro_client.fetch_product(product, lower.date(), upper.date() + timedelta(days=1))
        self._save(
            rows,
            product=product,
            source="macro_daily",
            interval="day",
            start=lower,
            end=upper,
            result=result,
        )

    def run(
        self,
        sources: Iterable[ExternalBarSource],
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, ExternalBarResult]:
        """独立推进各来源及原生周期，记录已提交条数和最近一次失败。"""
        self._now = datetime.now(UTC)
        sources_by_type: dict[ExternalBarSource, tuple[ExternalKLineSource, ...]] = {
            "sge_daily": ("sge_daily",),
            "dukascopy": ("dukascopy_bid", "dukascopy_ask"),
            "histdata": ("histdata_bid",),
            "macro_daily": ("macro_daily",),
        }
        reports: dict[str, ExternalBarResult] = {}
        for selected in dict.fromkeys(sources):
            if selected == "macro_daily":
                for product in MACRO_DAILY_PRODUCTS:
                    report = reports[f"macro_daily/{product}/day"] = ExternalBarResult()
                    try:
                        self._sync_macro_product(product, start, end, report)
                    except Exception as exc:
                        report.error = str(exc)
                continue
            for source in sources_by_type[selected]:
                interval = "day" if source == "sge_daily" else "m1"
                report = reports[f"{source}/{interval}"] = ExternalBarResult()
                try:
                    self._sync_sequence(source, interval, start, end, report)
                except Exception as exc:
                    report.error = str(exc)
        return reports
