"""宏观与汇率历史日线公开接口及本地 CSV 容灾读取客户端。"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import ClassVar

import httpx

from src.contracts.domains.market_bar import (
    MACRO_DXY_PRODUCT,
    MACRO_US10Y_PRODUCT,
    MACRO_USDCNY_PRODUCT,
    ExternalCandle,
)


class MacroBarClient:
    """拉取 USD/CNY、美元指数（DXY）与 10 年期美债收益率日线，支持本地 CSV 兜底。"""

    _FRANKFURTER_BASE: ClassVar[str] = "https://api.frankfurter.dev/v1"
    _FRED_CSV_BASE: ClassVar[str] = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    _CSV_FILE_MAP: ClassVar[dict[str, str]] = {
        MACRO_USDCNY_PRODUCT: "usdcny.csv",
        MACRO_DXY_PRODUCT: "dxy.csv",
        MACRO_US10Y_PRODUCT: "us10y.csv",
    }
    _AVAILABLE_HOUR_UTC: ClassVar[int] = 22
    FRED_SERIES_MAP: ClassVar[dict[str, str]] = {
        MACRO_DXY_PRODUCT: "DTWEXBGS",
        MACRO_US10Y_PRODUCT: "DGS10",
    }

    def __init__(self, http: httpx.Client, *, csv_dir: Path | None = None) -> None:
        self._http = http
        self._csv_dir = csv_dir

    @staticmethod
    def _parse_positive_float(raw: object) -> float | None:
        if raw is None or isinstance(raw, bool):
            return None
        text = str(raw).strip()
        if not text or text == ".":
            return None
        try:
            value = float(text)
        except ValueError:
            return None
        if not math.isfinite(value) or value <= 0.0:
            return None
        return value

    @classmethod
    def _make_candle(cls, day: date, value: float) -> ExternalCandle:
        """构造单日宏观 K 线，以 UTC 22:00（北京时间次日 06:00，早于 08:30 盘前）作为可用时刻。"""
        start = datetime.combine(day, time.min, UTC)
        end = datetime.combine(day, time(cls._AVAILABLE_HOUR_UTC, 0), UTC)
        return ExternalCandle(start, end, value, value, value, value)

    def _resolve_csv_path(self, product_code: str, csv_path: Path | None = None) -> Path | None:
        if csv_path is not None:
            return csv_path
        if self._csv_dir is None:
            return None
        filename = self._CSV_FILE_MAP.get(product_code)
        if filename is None:
            return None
        return self._csv_dir / filename

    def load_local_csv(
        self,
        product_code: str,
        *,
        csv_path: Path | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> Iterable[ExternalCandle]:
        """从本地 CSV（需含 date 与 close/value 列）读取宏观日线作为离线或容灾数据源。"""
        target = self._resolve_csv_path(product_code, csv_path)
        if target is None or not target.exists():
            return
        with target.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            for row in reader:
                raw_date = (row.get("date") or row.get("DATE") or row.get("observation_date") or "").strip()
                if not raw_date:
                    continue
                try:
                    day = date.fromisoformat(raw_date[:10])
                except ValueError:
                    continue
                if start is not None and day < start:
                    continue
                if end is not None and day >= end:
                    continue
                raw_val = row.get("close") or row.get("value") or row.get("CLOSE") or row.get("VALUE")
                val = self._parse_positive_float(raw_val)
                if val is None:
                    continue
                yield self._make_candle(day, val)

    def fetch_usdcny(self, start: date, end: date) -> Iterable[ExternalCandle]:
        """从 Frankfurter 公共汇率接口拉取 USD/CNY 历史日线。"""
        if start >= end:
            return
        url = f"{self._FRANKFURTER_BASE}/{start.isoformat()}..{end.isoformat()}"
        resp = self._http.get(url, params={"base": "USD", "symbols": "CNY"}).raise_for_status()
        payload = resp.json()
        rates = payload.get("rates") if isinstance(payload, dict) else None
        if not isinstance(rates, dict):
            return
        for raw_day in sorted(rates.keys()):
            try:
                day = date.fromisoformat(str(raw_day)[:10])
            except ValueError:
                continue
            if day < start or day >= end:
                continue
            entry = rates.get(raw_day)
            raw_val = entry.get("CNY") if isinstance(entry, dict) else None
            val = self._parse_positive_float(raw_val)
            if val is not None:
                yield self._make_candle(day, val)

    def fetch_fred_series(self, series_id: str, start: date, end: date) -> Iterable[ExternalCandle]:
        """从 FRED 公开 CSV 接口拉取指定宏观序列（如 DTWEXBGS、DGS10）。"""
        if start >= end:
            return
        resp = self._http.get(
            self._FRED_CSV_BASE,
            params={"id": series_id, "cosd": start.isoformat(), "coed": end.isoformat()},
        ).raise_for_status()
        reader = csv.reader(io.StringIO(resp.text))
        next(reader, None)
        for row in reader:
            if len(row) < 2:
                continue
            try:
                day = date.fromisoformat(row[0].strip()[:10])
            except ValueError:
                continue
            if day < start or day >= end:
                continue
            val = self._parse_positive_float(row[1])
            if val is not None:
                yield self._make_candle(day, val)

    def fetch_product(self, product_code: str, start: date, end: date) -> Iterable[ExternalCandle]:
        """按宏观品种编码拉取日线；若配置了本地 CSV 文件则优先读取，否则调用远端公开接口。"""
        local_csv = self._resolve_csv_path(product_code)
        if local_csv is not None and local_csv.exists():
            yield from self.load_local_csv(product_code, csv_path=local_csv, start=start, end=end)
            return
        if product_code == MACRO_USDCNY_PRODUCT:
            yield from self.fetch_usdcny(start, end)
            return
        series_id = self.FRED_SERIES_MAP.get(product_code)
        if series_id is None:
            raise ValueError(f"不支持的宏观品种代码：{product_code}")
        yield from self.fetch_fred_series(series_id, start, end)
