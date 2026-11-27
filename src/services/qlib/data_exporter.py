"""本地 SQLite 行情导出为 Qlib 二进制格式及外部 Qlib 数据集校验服务。"""

from __future__ import annotations

import bisect
import json
import math
import operator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import ClassVar, Literal
from zoneinfo import ZoneInfo

import numpy as np

from src.contracts.domains.market_bar import (
    MACRO_DXY_PRODUCT,
    MACRO_US10Y_PRODUCT,
    MACRO_USDCNY_PRODUCT,
    XAUUSD_MERGED_SOURCE,
    XAUUSD_ORIGIN_CODES,
    XAUUSD_PRODUCT,
    KLineBar,
)
from src.contracts.domains.product import FeeRule
from src.contracts.domains.qlib import QlibDatasetManifest
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.reference_quote import ReferenceProductCatalog
from src.contracts.enums.trading import DataQualityState
from src.contracts.exceptions import QlibDatasetError
from src.contracts.schemas.qlib import QlibExportFrequency, QlibExportReport, QlibExportRequest
from src.repositories.quote import QuoteRepository
from src.services.market.bar_collector import MarketBarCollector
from src.services.market.live_quotes import LiveQuoteCollector
from src.services.market.session_calendar import SessionCalendar


class QlibDataExporter:
    """将本地 SQLite 双边报价/参考行情/K 线按 PIT 导出为标准 Qlib 二进制目录，并支持外部目录校验。"""

    _SHANGHAI: ClassVar[ZoneInfo] = ZoneInfo("Asia/Shanghai")
    _KLINE_SOURCE_PRIORITY: ClassVar[dict[str, int]] = {
        "jd_public_kline": 2,
        "jd_sim_kline": 1,
    }
    _KLINE_INTERVAL_DELTAS: ClassVar[dict[str, timedelta]] = {
        "m1": timedelta(minutes=1),
        "m5": timedelta(minutes=5),
        "m15": timedelta(minutes=15),
        "m30": timedelta(minutes=30),
        "m60": timedelta(hours=1),
        "day": timedelta(days=1),
        "week": timedelta(days=7),
        "month": timedelta(days=30),
        "year": timedelta(days=365),
    }

    def __init__(
        self,
        quotes: QuoteRepository,
        calendar: SessionCalendar,
        *,
        live_quotes: LiveQuoteCollector | None = None,
        market_bars: MarketBarCollector | None = None,
        fee_rule: FeeRule | None = None,
        basis_reference_product: str = "SGE-Au99.99",
        default_product_code: str = "GOLD_ACCUMULATION",
        default_kline_products: tuple[str, ...] = ("CMBC-JCJ", "SGE-Au99.99", "WG-JDAU", "WG-XAUUSD"),
    ) -> None:
        self._quotes = quotes
        self._calendar = calendar
        self._live_quotes = live_quotes
        self._market_bars = market_bars
        self._fee_rule = fee_rule or FeeRule()
        self._basis_reference_product = basis_reference_product
        self._default_product_code = default_product_code
        self._default_kline_products = default_kline_products

    @staticmethod
    def _to_utc(dt: datetime) -> datetime:
        return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    @staticmethod
    def _is_iso_date(value: str) -> bool:
        if len(value) != 10:
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True

    @staticmethod
    def _bucket_key(dt: datetime, freq: QlibExportFrequency) -> str:
        utc_dt = dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        if freq == "day":
            return utc_dt.strftime("%Y-%m-%d")
        if freq == "1h":
            return utc_dt.strftime("%Y-%m-%d %H:00:00")
        return utc_dt.strftime("%Y-%m-%d %H:%M:00")

    @staticmethod
    def _qlib_freq_name(freq: QlibExportFrequency) -> str:
        return "60min" if freq == "1h" else freq

    @staticmethod
    def _write_qlib_bin(path: Path, start_index: int, values: list[float]) -> None:
        """按 Qlib 标准小端 float32 格式写入特征二进制文件（首元素为日历起始索引）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        arr = np.hstack([float(start_index), np.asarray(values, dtype=np.float32)]).astype("<f")
        arr.tofile(path)

    def _load_basis_series(self) -> tuple[list[datetime], list[float]]:
        if self._live_quotes is None:
            return [], []
        try:
            refs = self._live_quotes.query_series(self._basis_reference_product, limit=None, valid_only=True)
        except ValueError:
            return [], []
        pairs: list[tuple[datetime, float]] = []
        for item in refs:
            if item.unit != "CNY/g" or item.price <= 0 or item.quality_state == DataQualityState.ANOMALY:
                continue
            ts = self._to_utc(item.available_at or item.received_at)
            pairs.append((ts, item.price))
        pairs.sort(key=operator.itemgetter(0))
        return [p[0] for p in pairs], [p[1] for p in pairs]

    @staticmethod
    def _lookup_basis(times: list[datetime], prices: list[float], as_of: datetime) -> float:
        if not times:
            return float("nan")
        idx = bisect.bisect_right(times, as_of) - 1
        return prices[idx] if idx >= 0 else float("nan")

    @staticmethod
    def _lookup_pit_change(
        times: list[datetime],
        values: list[float],
        as_of: datetime,
        *,
        mode: Literal["return", "diff"],
    ) -> float:
        """按严格 PIT 截止时间查找最近两条已知观测并计算日收益率或日变动。"""
        if len(times) < 2:
            return float("nan")
        idx = bisect.bisect_right(times, as_of) - 1
        if idx < 1:
            return float("nan")
        curr = values[idx]
        prev = values[idx - 1]
        if not (math.isfinite(curr) and math.isfinite(prev)):
            return float("nan")
        if mode == "diff":
            return curr - prev
        if prev <= 0.0:
            return float("nan")
        return curr / prev - 1.0

    def _load_daily_close_series(
        self,
        product_code: str,
        *,
        sources: tuple[str, ...],
        bar_cache: dict[tuple[str, str, str], list[KLineBar]] | None = None,
    ) -> tuple[list[datetime], list[float]]:
        if self._market_bars is None:
            return [], []
        bars: list[KLineBar] = []
        for src in sources:
            cache_key = (product_code, "day", src)
            if bar_cache is not None and cache_key in bar_cache:
                fetched = bar_cache[cache_key]
            else:
                try:
                    fetched = self._market_bars.query_klines(product_code, "day", source=src, limit=None)
                except ValueError:
                    continue
                if bar_cache is not None and fetched:
                    bar_cache[cache_key] = fetched
            if fetched:
                bars = fetched
                break
        if not bars:
            return [], []
        by_ts: dict[datetime, float] = {}
        for bar in bars:
            if not math.isfinite(bar.close_price) or bar.close_price <= 0:
                continue
            ts = self._to_utc(bar.available_at) if bar.available_at else self._to_utc(bar.bar_time) + timedelta(days=1)
            by_ts[ts] = bar.close_price
        pairs = sorted(by_ts.items(), key=operator.itemgetter(0))
        return [p[0] for p in pairs], [p[1] for p in pairs]

    def _load_macro_series(
        self,
        bar_cache: dict[tuple[str, str, str], list[KLineBar]] | None = None,
    ) -> dict[str, tuple[list[datetime], list[float]]]:
        return {
            "xau": self._load_daily_close_series(
                XAUUSD_PRODUCT,
                sources=(XAUUSD_MERGED_SOURCE, "jd_public_kline", "jd_sim_kline"),
                bar_cache=bar_cache,
            ),
            "usdcny": self._load_daily_close_series(
                MACRO_USDCNY_PRODUCT,
                sources=("macro_daily",),
                bar_cache=bar_cache,
            ),
            "dxy": self._load_daily_close_series(
                MACRO_DXY_PRODUCT,
                sources=("macro_daily",),
                bar_cache=bar_cache,
            ),
            "us10y": self._load_daily_close_series(
                MACRO_US10Y_PRODUCT,
                sources=("macro_daily",),
                bar_cache=bar_cache,
            ),
        }

    def _premarket_cutoff_utc(self, bar: KLineBar, fallback: datetime) -> datetime:
        """返回北京时间交易日盘前 08:30 对应的 UTC 截止时刻，杜绝外盘/宏观前视偏差。"""
        if self._is_iso_date(bar.trade_date):
            day = date.fromisoformat(bar.trade_date)
            return datetime.combine(day, time(8, 30), self._SHANGHAI).astimezone(UTC)
        return fallback

    def _calendar_features(self, dt: datetime) -> tuple[float, float, float]:
        hours_to_close = self._calendar.get_hours_until_weekend_close(dt)
        is_risk = 1.0 if self._calendar.is_in_weekend_risk_window(dt) else 0.0
        hours_since_open = (
            max(self._calendar.trading_hours_per_week() - hours_to_close, 0.0)
            if self._calendar.is_trading_time(dt)
            else 0.0
        )
        return round(hours_to_close, 4), round(hours_since_open, 4), is_risk

    def _aggregate_quotes(
        self,
        quotes: list[QuoteRaw],
        *,
        freq: QlibExportFrequency,
        basis_times: list[datetime],
        basis_prices: list[float],
    ) -> dict[str, dict[str, float]]:
        buckets: dict[str, list[QuoteRaw]] = {}
        for quote in sorted(quotes, key=lambda q: self._to_utc(q.quote_at)):
            if (
                quote.quality_state != DataQualityState.NORMAL
                or not math.isfinite(quote.bid_price)
                or not math.isfinite(quote.ask_price)
                or quote.bid_price <= 0
                or quote.ask_price < quote.bid_price
            ):
                continue
            pit_dt = max(
                self._to_utc(quote.quote_at),
                self._to_utc(quote.received_at),
                self._to_utc(quote.available_at or quote.received_at),
            )
            if freq == "1h" and not self._calendar.is_trading_time(pit_dt):
                continue
            key = self._bucket_key(pit_dt, freq)
            buckets.setdefault(key, []).append(quote)

        rows: dict[str, dict[str, float]] = {}
        for key, items in buckets.items():
            mids = [(q.bid_price + q.ask_price) / 2.0 for q in items]
            last_q = items[-1]
            last_mid = mids[-1]
            spread = last_q.ask_price - last_q.bid_price
            spread_pct = spread / last_mid if last_mid > 0 else 0.0
            last_pit = max(
                self._to_utc(last_q.quote_at),
                self._to_utc(last_q.received_at),
                self._to_utc(last_q.available_at or last_q.received_at),
            )
            basis_ref = self._lookup_basis(basis_times, basis_prices, last_pit)
            bank_basis = (last_mid / basis_ref) - 1.0 if math.isfinite(basis_ref) and basis_ref > 0 else float("nan")
            h_close, h_open, risk_win = self._calendar_features(last_pit)
            rows[key] = {
                "open": mids[0],
                "high": max(mids),
                "low": min(mids),
                "close": last_mid,
                "bid": last_q.bid_price,
                "ask": last_q.ask_price,
                "spread": spread,
                "spread_pct": spread_pct,
                "bank_basis": bank_basis,
                "hours_to_weekend_close": h_close,
                "hours_since_open": h_open,
                "is_weekend_risk_window": risk_win,
            }
        return rows

    def _aggregate_klines(
        self,
        bars: list[KLineBar],
        *,
        freq: QlibExportFrequency,
        spread_pct: float,
        basis_times: list[datetime],
        basis_prices: list[float],
        macro_series: dict[str, tuple[list[datetime], list[float]]] | None = None,
    ) -> dict[str, dict[str, float]]:
        half = spread_pct / 2.0
        now_utc = datetime.now(UTC)
        rows: dict[str, dict[str, float]] = {}
        best_ranks: dict[str, tuple[int, datetime, datetime]] = {}
        for bar in sorted(bars, key=lambda b: self._to_utc(b.bar_time)):
            bar_dt = self._to_utc(bar.bar_time)
            bar_span = self._KLINE_INTERVAL_DELTAS.get(bar.interval, timedelta(hours=1 if freq == "1h" else 24))
            bar_end_dt = self._to_utc(bar.available_at) if bar.available_at else bar_dt + bar_span
            # 排除尚未收盘的当期不完整 K 线，并按柱结束时刻查询收盘基差参考价
            if bar_end_dt > now_utc:
                continue
            key = (
                bar.trade_date
                if freq == "day" and bar.interval == "day" and self._is_iso_date(bar.trade_date)
                else self._bucket_key(bar_dt, freq)
            )
            rank = (
                self._KLINE_SOURCE_PRIORITY.get(bar.source, 0),
                self._to_utc(bar.collected_at),
                bar_dt,
            )
            prev_rank = best_ranks.get(key)
            if prev_rank is not None and rank < prev_rank:
                continue
            best_ranks[key] = rank
            bid = bar.close_price * (1.0 - half)
            ask = bar.close_price * (1.0 + half)
            basis_ref = (
                float("nan")
                if bar.product_code == XAUUSD_PRODUCT
                else self._lookup_basis(basis_times, basis_prices, bar_end_dt)
            )
            bank_basis = (
                (bar.close_price / basis_ref) - 1.0 if math.isfinite(basis_ref) and basis_ref > 0 else float("nan")
            )
            h_close, h_open, risk_win = self._calendar_features(bar_end_dt)
            row: dict[str, float] = {
                "open": bar.open_price,
                "high": bar.high_price,
                "low": bar.low_price,
                "close": bar.close_price,
                "volume": bar.volume if bar.volume is not None else 0.0,
                "turnover": bar.turnover if bar.turnover is not None else 0.0,
                "factor": 1.0,
                "bid": bid,
                "ask": ask,
                "spread": ask - bid,
                "spread_pct": spread_pct,
                "bank_basis": bank_basis,
                "hours_to_weekend_close": h_close,
                "hours_since_open": h_open,
                "is_weekend_risk_window": risk_win,
            }
            if macro_series is not None and freq == "day":
                cutoff_utc = self._premarket_cutoff_utc(bar, bar_dt)
                row["xau_ret"] = self._lookup_pit_change(*macro_series["xau"], cutoff_utc, mode="return")
                row["usdcny_ret"] = self._lookup_pit_change(*macro_series["usdcny"], cutoff_utc, mode="return")
                row["dxy_ret"] = self._lookup_pit_change(*macro_series["dxy"], cutoff_utc, mode="return")
                row["us10y_diff"] = self._lookup_pit_change(*macro_series["us10y"], cutoff_utc, mode="diff")
            if bar.origin_source:
                origin_code = XAUUSD_ORIGIN_CODES.get(bar.origin_source)
                if origin_code is not None:
                    row["origin_source"] = origin_code
            rows[key] = row
        return rows

    def _resolve_instruments(self, request: QlibExportRequest) -> list[str]:
        if request.instruments:
            return list(dict.fromkeys(code.strip() for code in request.instruments if code.strip()))
        if request.source_mode == "quotes":
            return [self._default_product_code]
        if request.source_mode == "reference":
            return [item.code for item in ReferenceProductCatalog.all() if item.unit == "CNY/g"]
        if request.kline_source == XAUUSD_MERGED_SOURCE or request.freq.endswith("min"):
            return [XAUUSD_PRODUCT]
        return list(self._default_kline_products)

    def _load_instrument_rows(
        self,
        instrument: str,
        request: QlibExportRequest,
        basis_times: list[datetime],
        basis_prices: list[float],
        macro_series: dict[str, tuple[list[datetime], list[float]]] | None = None,
        bar_cache: dict[tuple[str, str, str], list[KLineBar]] | None = None,
    ) -> dict[str, dict[str, float]]:
        now_utc = datetime.now(UTC)
        if request.source_mode == "quotes":
            quotes = self._quotes.query_pit_series(
                start_time=datetime(1970, 1, 1, tzinfo=UTC),
                end_time=now_utc,
                as_of=now_utc,
                product_code=instrument,
                exclude_synthetic=request.exclude_synthetic,
            )
            return self._aggregate_quotes(quotes, freq=request.freq, basis_times=basis_times, basis_prices=basis_prices)
        if request.source_mode == "reference":
            if self._live_quotes is None:
                raise QlibDatasetError("未配置参考行情采集器，无法从参考行情库导出 Qlib 数据集")
            try:
                refs = self._live_quotes.query_series(instrument, limit=None, valid_only=True)
            except ValueError:
                return {}
            half = request.spread_pct / 2.0
            synthetic_quotes = [
                QuoteRaw(
                    quote_id=f"qlib_ref_{item.product_code}_{item.quote_id}",
                    source_id=f"QLIB_REF_{item.product_code}",
                    product_code=item.product_code,
                    bid_price=round(item.price * (1.0 - half), 4),
                    ask_price=round(item.price * (1.0 + half), 4),
                    quote_at=item.quote_at or item.received_at,
                    received_at=item.received_at,
                    available_at=item.available_at or item.received_at,
                    is_tradable=True,
                    is_synthetic=True,
                    payload_hash=item.payload_hash,
                    quality_state=DataQualityState.NORMAL,
                )
                for item in refs
                if item.unit == "CNY/g"
                and item.price > 0
                and item.quality_state not in (DataQualityState.ANOMALY, DataQualityState.STALE)
            ]
            return self._aggregate_quotes(
                synthetic_quotes,
                freq=request.freq,
                basis_times=basis_times,
                basis_prices=basis_prices,
            )
        if self._market_bars is None:
            raise QlibDatasetError("未配置多周期 K 线采集器，无法从 K 线库导出 Qlib 数据集")
        interval = request.kline_interval
        merged = instrument == XAUUSD_PRODUCT and request.kline_source in (None, XAUUSD_MERGED_SOURCE)
        if merged:
            if request.freq == "day":
                interval = "day"
            else:
                interval = f"m{self._qlib_freq_name(request.freq).removesuffix('min')}"
        source = request.kline_source
        cache_key = (instrument, interval, XAUUSD_MERGED_SOURCE) if merged else None
        if cache_key is not None and bar_cache is not None and cache_key in bar_cache:
            bars = bar_cache[cache_key]
        else:
            try:
                bars = self._market_bars.query_klines(
                    instrument,
                    interval,
                    source=source if merged or source else ("jd_public_kline", "jd_sim_kline"),
                    limit=None if merged else 100000,
                )
            except ValueError:
                return {}
            if cache_key is not None and bar_cache is not None and bars:
                bar_cache[cache_key] = bars
        return self._aggregate_klines(
            bars,
            freq=request.freq,
            spread_pct=request.spread_pct,
            basis_times=basis_times,
            basis_prices=basis_prices,
            macro_series=macro_series,
        )

    def export_local_dataset(self, request: QlibExportRequest, *, target_dir: Path) -> QlibExportReport:
        """将本地 SQLite 行情导出为标准 Qlib 二进制目录并生成清单文件。"""
        candidates = self._resolve_instruments(request)
        if request.freq.endswith("min") and (
            request.source_mode != "kline"
            or request.kline_source not in (None, XAUUSD_MERGED_SOURCE)
            or any(code != XAUUSD_PRODUCT for code in candidates)
        ):
            raise QlibDatasetError("分钟频须使用 XAUUSD 合并 K 线")
        if not candidates:
            raise QlibDatasetError("未指定可导出的品种列表")

        need_basis = request.source_mode != "kline" or any(code != XAUUSD_PRODUCT for code in candidates)
        basis_times, basis_prices = self._load_basis_series() if need_basis else ([], [])
        bar_cache: dict[tuple[str, str, str], list[KLineBar]] = {}
        macro_series = (
            self._load_macro_series(bar_cache=bar_cache)
            if request.include_macro_features and request.source_mode == "kline" and request.freq == "day"
            else None
        )
        rows_by_instrument: dict[str, dict[str, dict[str, float]]] = {}
        skipped: list[str] = []
        for code in candidates:
            rows = self._load_instrument_rows(code, request, basis_times, basis_prices, macro_series, bar_cache)
            if rows:
                rows_by_instrument[code] = rows
            else:
                skipped.append(code)

        if not rows_by_instrument:
            raise QlibDatasetError(f"在 {request.source_mode} 模式下未查询到任何可导出的有效历史行情")

        calendar = sorted({ts for rows in rows_by_instrument.values() for ts in rows})
        calendar_index = {ts: idx for idx, ts in enumerate(calendar)}
        qlib_freq = self._qlib_freq_name(request.freq)

        target_dir.mkdir(parents=True, exist_ok=True)
        calendars_dir = target_dir / "calendars"
        instruments_dir = target_dir / "instruments"
        features_dir = target_dir / "features"
        calendars_dir.mkdir(parents=True, exist_ok=True)
        instruments_dir.mkdir(parents=True, exist_ok=True)
        features_dir.mkdir(parents=True, exist_ok=True)

        written_files = 0
        (calendars_dir / f"{qlib_freq}.txt").write_text("\n".join(calendar) + "\n", encoding="utf-8")
        written_files += 1

        instrument_lines: list[str] = []
        exported_fields: set[str] = set()
        for code, rows in rows_by_instrument.items():
            inst_times = sorted(rows.keys())
            start_ts = inst_times[0]
            end_ts = inst_times[-1]
            instrument_lines.append(f"{code}\t{start_ts}\t{end_ts}")

            start_idx = calendar_index[start_ts]
            end_idx = calendar_index[end_ts]
            span_keys = calendar[start_idx : end_idx + 1]
            inst_fields = sorted({field for row in rows.values() for field in row})
            exported_fields.update(inst_fields)
            inst_dir = features_dir / code.lower()
            for field in inst_fields:
                values = [rows[ts][field] if ts in rows and field in rows[ts] else float("nan") for ts in span_keys]
                self._write_qlib_bin(inst_dir / f"{field}.{qlib_freq}.bin", start_idx, values)
                written_files += 1

        instruments_payload = "\n".join(instrument_lines) + "\n"
        (instruments_dir / "all.txt").write_text(instruments_payload, encoding="utf-8")
        (instruments_dir / "gold.txt").write_text(instruments_payload, encoding="utf-8")
        written_files += 2

        manifest = QlibDatasetManifest(
            source_type="local_sqlite",
            source_mode=request.source_mode,
            provider_uri=str(target_dir),
            freq=request.freq,
            instruments=list(rows_by_instrument.keys()),
            fields=sorted(exported_fields),
            calendar_count=len(calendar),
            start_time=calendar[0],
            end_time=calendar[-1],
            spread_pct=request.spread_pct,
            buy_fee_rate=self._fee_rule.buy_fee_rate,
            sell_fee_rate=self._fee_rule.sell_fee_rate,
            exported_at=datetime.now(UTC),
        )
        (target_dir / "dataset_manifest.json").write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
        written_files += 1
        return QlibExportReport(
            manifest=manifest,
            written_files=written_files,
            skipped_instruments=skipped,
        )

    def inspect_dataset(
        self,
        provider_uri: Path,
        *,
        source_type: Literal["local_sqlite", "external_qlib"],
    ) -> QlibDatasetManifest | None:
        """读取本地导出清单或校验外部标准 Qlib 目录并返回元信息，目录不存在时返回 None。"""
        if not provider_uri.exists() or not provider_uri.is_dir():
            return None
        manifest_path = provider_uri / "dataset_manifest.json"
        if source_type == "local_sqlite" and manifest_path.exists():
            return QlibDatasetManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))

        calendars_dir = provider_uri / "calendars"
        instruments_dir = provider_uri / "instruments"
        features_dir = provider_uri / "features"
        if not (calendars_dir.is_dir() and instruments_dir.is_dir() and features_dir.is_dir()):
            return None

        cal_files = sorted(calendars_dir.glob("*.txt"))
        if not cal_files:
            return None
        cal_file = cal_files[0]
        raw_freq = cal_file.stem
        freq = "1h" if raw_freq == "60min" else raw_freq
        cal_lines = [line.strip() for line in cal_file.read_text(encoding="utf-8").splitlines() if line.strip()]

        instruments: list[str] = []
        all_inst_file = instruments_dir / "all.txt"
        inst_file = all_inst_file if all_inst_file.exists() else next(iter(sorted(instruments_dir.glob("*.txt"))), None)
        if inst_file is not None:
            for line in inst_file.read_text(encoding="utf-8").splitlines():
                clean = line.strip()
                if clean:
                    instruments.append(clean.split()[0])

        fields: set[str] = set()
        for sub in sorted(features_dir.iterdir()):
            if sub.is_dir():
                for bin_file in sub.glob("*.bin"):
                    parts = bin_file.name.split(".")
                    if len(parts) >= 3:
                        fields.add(parts[0])
                if fields:
                    break

        return QlibDatasetManifest(
            source_type=source_type,
            source_mode="external" if source_type == "external_qlib" else "quotes",
            provider_uri=str(provider_uri),
            freq=freq,
            instruments=instruments,
            fields=sorted(fields),
            calendar_count=len(cal_lines),
            start_time=cal_lines[0] if cal_lines else None,
            end_time=cal_lines[-1] if cal_lines else None,
            exported_at=datetime.now(UTC),
        )
