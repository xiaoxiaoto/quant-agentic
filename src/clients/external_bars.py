"""外部黄金行情历史接口及原始格式转换。"""

import csv
import io
import lzma
import re
import struct
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import ClassVar, Literal
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo

import httpx

from src.contracts.domains.market_bar import ExternalCandle


class ExternalBarClient:
    """通过已建立的 HTTP 会话获取各来源的历史 K 线。"""

    _DUKASCOPY_ARCHIVE: ClassVar[str] = "https://raw.githubusercontent.com/3650326613-png/dukascopy_xauusd_1m_data/main"
    _DUKASCOPY_LATER_ARCHIVE: ClassVar[str] = "https://raw.githubusercontent.com/kevingtlin/Market-Data-Lab/main"
    _DUKASCOPY_LATER_START: ClassVar[datetime] = datetime(2026, 8, 1, tzinfo=UTC)
    _SHANGHAI: ClassVar[ZoneInfo] = ZoneInfo("Asia/Shanghai")
    _MINUTE: ClassVar[timedelta] = timedelta(minutes=1)
    _DEFAULT_TIMEOUT_SECONDS: ClassVar[float] = 15.0
    _DEFAULT_RETRIES: ClassVar[int] = 1
    _DEFAULT_HEADERS: ClassVar[dict[str, str]] = {"User-Agent": "Mozilla/5.0"}
    DUKASCOPY_CUTOFF: ClassVar[datetime] = datetime(2026, 10, 19, 14, tzinfo=UTC)
    HISTDATA_TZ: ClassVar[timezone] = timezone(timedelta(hours=-5))

    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    @classmethod
    def _dukascopy_csv(cls, text: str) -> Iterable[ExternalCandle]:
        for row in csv.DictReader(io.StringIO(text)):
            stamp = datetime.fromtimestamp(int(row["timestamp"]) / 1000, UTC)
            yield ExternalCandle(
                stamp,
                stamp + cls._MINUTE,
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
            )

    @classmethod
    def _dukascopy_binary(cls, payload: bytes, day: date) -> Iterable[ExternalCandle]:
        midnight = datetime.combine(day, time.min, UTC)
        for seconds, opened, closed, low, high, volume in struct.iter_unpack(">IIIIIf", lzma.decompress(payload)):
            stamp = midnight + timedelta(seconds=seconds)
            yield ExternalCandle(
                stamp, stamp + cls._MINUTE, opened / 1000, high / 1000, low / 1000, closed / 1000, volume
            )

    @classmethod
    def _histdata_csv(cls, stream: Iterable[str]) -> Iterable[ExternalCandle]:
        for row in csv.reader(stream, delimiter=";"):
            if not row:
                continue
            if len(row) < 5:
                raise ValueError("HistData CSV 行列数不足")
            stamp = datetime.strptime(row[0], "%Y%m%d %H%M%S").replace(tzinfo=cls.HISTDATA_TZ)
            stamp = stamp.astimezone(UTC)
            yield ExternalCandle(stamp, stamp + cls._MINUTE, float(row[1]), float(row[2]), float(row[3]), float(row[4]))

    @classmethod
    def _histdata_rows(cls, payload: bytes) -> Iterable[ExternalCandle]:
        with ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError("HistData 压缩包应包含一个 CSV")
            with archive.open(names[0]) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig") as stream:
                yield from cls._histdata_csv(stream)

    @classmethod
    def _sge_day(cls, day: date) -> tuple[datetime, datetime]:
        return datetime.combine(day, time.min, cls._SHANGHAI), datetime.combine(day, time(15, 30), cls._SHANGHAI)

    @classmethod
    def create_http_client(cls, *, timeout: float | None = None) -> httpx.Client:
        """创建带默认超时、重试与请求头的外部行情 HTTP 会话。"""
        return httpx.Client(
            timeout=cls._DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout,
            transport=httpx.HTTPTransport(retries=cls._DEFAULT_RETRIES),
            headers=dict(cls._DEFAULT_HEADERS),
        )

    @classmethod
    def histdata_tz(cls) -> timezone:
        """返回 HistData 归档划分使用的固定时区。"""
        return cls.HISTDATA_TZ

    @classmethod
    def histdata_upper_bound(cls, now: datetime) -> datetime:
        """返回 HistData 当月归档之前的最新完整可用截止时刻。"""
        local = now.astimezone(cls.HISTDATA_TZ)
        return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)

    @classmethod
    def dukascopy_segments(cls, start: datetime, end: datetime) -> tuple[tuple[datetime, datetime], ...]:
        """按存档截止日拆分拉取区间，便于上层分段提交并在源站异常后断点续补。"""
        if start < cls.DUKASCOPY_CUTOFF < end:
            return ((start, cls.DUKASCOPY_CUTOFF), (cls.DUKASCOPY_CUTOFF, end))
        return ((start, end),)

    def sge(self) -> Iterable[ExternalCandle]:
        """获取 Au99.99 官方日线。"""
        response = self._http.post(
            "https://www.sge.com.cn/graph/Dailyhq",
            headers={"Referer": "https://www.sge.com.cn/sjzx/mrhq", "X-Requested-With": "XMLHttpRequest"},
            data={"instid": "Au99.99"},
        ).raise_for_status()
        for label, opened, closed, low, high in response.json()["time"]:
            stamp, end = self._sge_day(date.fromisoformat(label))
            yield ExternalCandle(stamp, end, float(opened), float(high), float(low), float(closed))

    def dukascopy(self, side: Literal["bid", "ask"], start: datetime, end: datetime) -> Iterable[ExternalCandle]:
        """按 UTC 月读取历史存档，存档末尾之后从源站按日补齐。"""
        if side not in ("bid", "ask"):
            raise ValueError(f"dukascopy 方向仅支持 bid 或 ask，收到：{side}")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("dukascopy 起始与结束时间必须包含时区信息")
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        if start >= end:
            return
        cutoff = self.DUKASCOPY_CUTOFF
        if start < cutoff:
            archive_end = min(end, cutoff)
            cursor = datetime(start.year, start.month, 1, tzinfo=UTC)
            while cursor < archive_end:
                month = cursor.strftime("%Y_%m")
                archive = (
                    self._DUKASCOPY_LATER_ARCHIVE if cursor >= self._DUKASCOPY_LATER_START else self._DUKASCOPY_ARCHIVE
                )
                url = f"{archive}/xauusd/{side}/m1/xauusd_{side}_m1_{month}.csv"
                response = self._http.get(url).raise_for_status()
                yield from self._dukascopy_csv(response.text)
                next_year = cursor.year + (cursor.month == 12)
                cursor = datetime(next_year, cursor.month % 12 + 1, 1, tzinfo=UTC)
        if end <= cutoff:
            return

        day = max(start, cutoff).date()
        while datetime.combine(day, time.min, UTC) < end:
            if day.weekday() != 5:
                url = (
                    f"https://datafeed.dukascopy.com/datafeed/XAUUSD/{day.year}/{day.month - 1:02d}"
                    f"/{day.day:02d}/{side.upper()}_candles_min_1.bi5"
                )
                payload = self._http.get(url).raise_for_status().content
                if payload:
                    yield from self._dukascopy_binary(payload, day)
            day += timedelta(days=1)

    def histdata(self, year: int, month: int | None) -> Iterable[ExternalCandle]:
        """从 HistData 获取年度或月度买价分钟线，允许使用同源公开备份。"""
        if month is not None and not 1 <= month <= 12:
            raise ValueError(f"HistData 月份必须在 1 到 12 之间，收到：{month}")
        base = "https://www.histdata.com"
        period = f"{year}/{month}" if month is not None else str(year)
        archive_id = f"{year}{month:02d}" if month is not None else str(year)
        url = f"{base}/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/XAUUSD/{period}"
        yielded = False
        try:
            page = self._http.get(url).raise_for_status()
            token = re.search(r'name="tk"[^>]*value="([a-fA-F0-9]{32})"', page.text)
            if token is None:
                raise ValueError(f"HistData 未提供 {period} 下载表单")
            response = self._http.post(
                f"{base}/get.php",
                headers={"Referer": url},
                data={
                    "tk": token[1],
                    "date": str(year),
                    "datemonth": archive_id,
                    "platform": "ASCII",
                    "timeframe": "M1",
                    "fxpair": "XAUUSD",
                },
            ).raise_for_status()
            for candle in self._histdata_rows(response.content):
                yielded = True
                yield candle
        except (httpx.HTTPError, ValueError, BadZipFile) as exc:
            if yielded or not (2009 <= year <= 2025 and month is None or (year == 2026 and month in (1, 2))):
                raise
            url = f"https://raw.githubusercontent.com/nousx/xauusd-data/main/DAT_ASCII_XAUUSD_M1_{archive_id}.csv"
            with self._http.stream("GET", url) as backup:
                try:
                    backup.raise_for_status()
                    yield from self._histdata_csv(backup.iter_lines())
                except httpx.HTTPError:
                    raise exc from None
