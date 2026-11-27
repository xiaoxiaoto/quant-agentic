"""TimesFM 多因子数据准备服务：按 PIT 对齐等频序列并构造协变量、截面因子与扣费净收益标签。"""

from __future__ import annotations

import bisect
import json
import math
import operator
from collections import OrderedDict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, ClassVar, cast
from uuid import uuid4

import numpy as np

from src.contracts.domains.market_bar import XAUUSD_MERGED_SOURCE, XAUUSD_PRODUCT, KLineBar
from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.timesfm_multifactor import (
    TimesFMDatasetManifest,
    TimesFMFactorPreset,
    TimesFMFreq,
)
from src.contracts.enums.trading import DataQualityState, HoldingPeriod
from src.contracts.exceptions import TimesFMDatasetError
from src.contracts.schemas.timesfm_multifactor import TimesFMPrepareReport, TimesFMPrepareRequest
from src.repositories.quote import QuoteRepository
from src.services.market.bar_collector import MarketBarCollector
from src.services.market.live_quotes import LiveQuoteCollector
from src.services.market.session_calendar import SessionCalendar

_BarRow = tuple[datetime, float, float, float, float, float, float, float]


class TimesFMDatasetBuilder:
    """从本地 SQLite 行情构造 TimesFM 多因子数据集（序列、协变量、截面因子与标签）。"""

    _STEPS: ClassVar[dict[str, dict[HoldingPeriod, int]]] = {
        "1h": {
            HoldingPeriod.H1: 1,
            HoldingPeriod.H4: 4,
            HoldingPeriod.D1: 24,
            HoldingPeriod.D3: 72,
            HoldingPeriod.W1: 168,
            HoldingPeriod.W2: 336,
            HoldingPeriod.M1: 720,
        },
        "day": {
            HoldingPeriod.H1: 1,
            HoldingPeriod.H4: 1,
            HoldingPeriod.D1: 1,
            HoldingPeriod.D3: 3,
            HoldingPeriod.W1: 5,
            HoldingPeriod.W2: 10,
            HoldingPeriod.M1: 21,
        },
    }
    _FREQ_SECONDS: ClassVar[dict[str, float]] = {"1h": 3600.0, "day": 86400.0}
    _RETURN_STEPS: ClassVar[dict[str, tuple[int, int, int]]] = {"1h": (24, 72, 168), "day": (1, 3, 5)}
    _VOL_WINDOW: ClassVar[dict[str, int]] = {"1h": 24, "day": 20}
    _VOL_SCALE: ClassVar[dict[str, float]] = {"1h": 93.5948, "day": 19.1049}
    _KLINE_DEFAULT_INTERVAL: ClassVar[dict[str, str]] = {"1h": "m60", "day": "day"}
    _VALID_KLINE_INTERVALS: ClassVar[dict[str, frozenset[str]]] = {
        "1h": frozenset({"m60"}),
        "day": frozenset({"day"}),
    }
    _KLINE_SOURCE_PRIORITY: ClassVar[dict[str, int]] = {"jd_public_kline": 2, "jd_sim_kline": 1}
    _CALENDAR_CACHE_SIZE: ClassVar[int] = 8192
    _KLINE_DELTAS: ClassVar[dict[str, timedelta]] = {
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

    MANIFEST_NAME: ClassVar[str] = "dataset_manifest.json"
    SERIES_NAME: ClassVar[str] = "series.npz"
    PAST_ONLY_BASE: ClassVar[tuple[str, ...]] = (
        "log_return_1",
        "return_1d",
        "return_3d",
        "return_1w",
        "realized_volatility",
        "atr_pct",
        "spread_pct",
        "bank_basis",
        "momentum_zscore",
        "range_pos_20",
    )
    PAST_ONLY_OHLCV_EXTRA: ClassVar[tuple[str, ...]] = ("hl_range_pct", "body_pct", "volume_log")
    FUTURE_FACTORS: ClassVar[tuple[str, ...]] = (
        "hour_sin",
        "hour_cos",
        "dow_sin",
        "dow_cos",
        "hours_to_weekend_close_norm",
        "is_weekend_risk_window",
    )
    TABULAR_FACTORS: ClassVar[tuple[str, ...]] = (
        "return_1d",
        "return_3d",
        "return_1w",
        "realized_volatility",
        "atr",
        "spread",
        "spread_pct",
        "bank_basis",
        "hours_to_weekend_close",
        "hours_since_open",
        "is_weekend_risk_window",
        "event_risk_flag_count",
    )
    MIN_CONTEXT: ClassVar[int] = 32

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
    ) -> None:
        self._quotes = quotes
        self._calendar = calendar
        self._live_quotes = live_quotes
        self._market_bars = market_bars
        self._fee_rule = fee_rule or FeeRule()
        self._basis_reference_product = basis_reference_product
        self._default_product_code = default_product_code
        self._calendar_cache: OrderedDict[int, tuple[float, ...]] = OrderedDict()
        self._calendar_lock = Lock()

    @staticmethod
    def _to_utc(dt: datetime) -> datetime:
        return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    @staticmethod
    def _window_stat(values: np.ndarray, window: int, func: Callable[..., Any]) -> np.ndarray:
        """对序列做因果滚动统计，不足窗口处填 NaN。"""
        out = np.full(values.shape[0], np.nan, dtype=np.float64)
        if values.shape[0] < window:
            return out
        view = np.lib.stride_tricks.sliding_window_view(values, window)
        out[window - 1 :] = func(view, axis=1)
        return out

    @staticmethod
    def _shift_return(prices: np.ndarray, lag: int) -> np.ndarray:
        out = np.full(prices.shape[0], np.nan, dtype=np.float64)
        if prices.shape[0] > lag:
            out[lag:] = prices[lag:] / prices[:-lag] - 1.0
        return out

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
            pairs.append((self._to_utc(item.available_at or item.received_at), item.price))
        pairs.sort(key=operator.itemgetter(0))
        return [p[0] for p in pairs], [p[1] for p in pairs]

    @staticmethod
    def _lookup_basis(times: list[datetime], prices: list[float], as_of: datetime) -> float:
        if not times:
            return float("nan")
        idx = bisect.bisect_right(times, as_of) - 1
        return prices[idx] if idx >= 0 else float("nan")

    def _quotes_to_rows(self, quotes: list[QuoteRaw], freq: TimesFMFreq) -> list[_BarRow]:
        """将双边报价按 PIT 可用时刻聚合为等频柱。"""
        buckets: dict[str, list[tuple[datetime, QuoteRaw]]] = {}
        stamped: list[tuple[datetime, QuoteRaw]] = []
        for quote in quotes:
            if (
                quote.quality_state != DataQualityState.NORMAL
                or not math.isfinite(quote.bid_price)
                or not math.isfinite(quote.ask_price)
                or quote.bid_price <= 0
                or quote.ask_price < quote.bid_price
            ):
                continue
            pit = max(
                self._to_utc(quote.quote_at),
                self._to_utc(quote.received_at),
                self._to_utc(quote.available_at or quote.received_at),
            )
            stamped.append((pit, quote))
        stamped.sort(key=operator.itemgetter(0))
        for pit, quote in stamped:
            if freq == "1h" and not self._calendar.is_trading_time(pit):
                continue
            key = pit.strftime("%Y-%m-%d") if freq == "day" else pit.strftime("%Y-%m-%d %H")
            buckets.setdefault(key, []).append((pit, quote))
        rows: list[_BarRow] = []
        for items in buckets.values():
            mids = [(q.bid_price + q.ask_price) / 2.0 for _, q in items]
            last_pit, last_q = items[-1]
            rows.append((last_pit, mids[0], max(mids), min(mids), mids[-1], 0.0, last_q.bid_price, last_q.ask_price))
        return rows

    def _reference_to_quotes(self, product_code: str, spread_pct: float) -> list[QuoteRaw]:
        if self._live_quotes is None:
            raise TimesFMDatasetError("未配置参考行情采集器，无法从参考行情库构建数据集")
        try:
            refs = self._live_quotes.query_series(product_code, limit=None, valid_only=True)
        except ValueError as exc:
            raise TimesFMDatasetError(str(exc)) from exc
        half = spread_pct / 2.0
        return [
            QuoteRaw(
                quote_id=f"tfm_ref_{item.product_code}_{item.quote_id}",
                source_id=f"TFM_REF_{item.product_code}",
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

    def _klines_to_rows(self, bars: list[KLineBar], freq: TimesFMFreq, spread_pct: float) -> list[_BarRow]:
        half = spread_pct / 2.0
        now_utc = datetime.now(UTC)
        rows: list[_BarRow] = []
        for bar in sorted(bars, key=lambda b: self._to_utc(b.bar_time)):
            bar_dt = self._to_utc(bar.bar_time)
            span = self._KLINE_DELTAS.get(bar.interval, timedelta(hours=1 if freq == "1h" else 24))
            end_dt = self._to_utc(bar.available_at) if bar.available_at else bar_dt + span
            if end_dt > now_utc:
                continue
            close = bar.close_price
            rows.append(
                (
                    end_dt,
                    bar.open_price,
                    bar.high_price,
                    bar.low_price,
                    close,
                    bar.volume if bar.volume is not None else 0.0,
                    close * (1.0 - half),
                    close * (1.0 + half),
                )
            )
        return rows

    def _load_rows(self, request: TimesFMPrepareRequest, product_code: str) -> list[_BarRow]:
        if request.source_mode == "quotes":
            now_utc = datetime.now(UTC)
            quotes = self._quotes.query_pit_series(
                start_time=datetime(1970, 1, 1, tzinfo=UTC),
                end_time=now_utc,
                as_of=now_utc,
                product_code=product_code,
                exclude_synthetic=request.exclude_synthetic,
            )
            return self._quotes_to_rows(quotes, request.freq)
        if request.source_mode == "reference":
            return self._quotes_to_rows(self._reference_to_quotes(product_code, request.spread_pct), request.freq)
        if self._market_bars is None:
            raise TimesFMDatasetError("未配置多周期 K 线采集器，无法从 K 线库构建数据集")
        merged = product_code == XAUUSD_PRODUCT and request.kline_source in (None, XAUUSD_MERGED_SOURCE)
        interval = request.kline_interval or self._KLINE_DEFAULT_INTERVAL[request.freq]
        if merged:
            valid_intervals = self._VALID_KLINE_INTERVALS[request.freq]
            if request.kline_interval is not None and request.kline_interval not in valid_intervals:
                raise TimesFMDatasetError(f"频率 {request.freq} 与源 K 线周期 {request.kline_interval} 不一致")
            interval = "day" if request.freq == "day" else "m60"
        elif interval not in self._VALID_KLINE_INTERVALS[request.freq]:
            raise TimesFMDatasetError(f"频率 {request.freq} 与源 K 线周期 {interval} 不一致")
        source = request.kline_source or None
        try:
            bars = self._market_bars.query_klines(
                product_code,
                interval,
                source=XAUUSD_MERGED_SOURCE if merged else (source or ("jd_public_kline", "jd_sim_kline")),
                limit=None if merged else 100000,
            )
        except ValueError as exc:
            raise TimesFMDatasetError(str(exc)) from exc
        if not merged and source is None:
            bars = self._dedupe_multi_source_klines(bars, request.freq)
        return self._klines_to_rows(bars, request.freq, request.spread_pct)

    def _dedupe_multi_source_klines(self, bars: list[KLineBar], freq: TimesFMFreq) -> list[KLineBar]:
        """多来源 K 线按频率时间桶与来源优先级去重，防止同一交易日或小时产生双行。"""
        selected: dict[object, KLineBar] = {}
        for bar in bars:
            bar_utc = self._to_utc(bar.bar_time)
            bucket: object = bar.trade_date if freq == "day" else bar_utc.replace(minute=0, second=0, microsecond=0)
            existing = selected.get(bucket)
            if existing is None:
                selected[bucket] = bar
                continue
            cand_prio = self._KLINE_SOURCE_PRIORITY.get(bar.source, 0)
            exist_prio = self._KLINE_SOURCE_PRIORITY.get(existing.source, 0)
            if cand_prio > exist_prio or (
                cand_prio == exist_prio and self._to_utc(bar.collected_at) >= self._to_utc(existing.collected_at)
            ):
                selected[bucket] = bar
        return sorted(selected.values(), key=lambda b: self._to_utc(b.bar_time))

    def _calendar_tuple(self, epoch: float) -> tuple[float, ...]:
        """返回时刻对应的 8 维日历特征：前 6 维为已知未来协变量，后 2 维为截面因子。"""
        key = int(epoch // 60)
        with self._calendar_lock:
            cached = self._calendar_cache.get(key)
            if cached is not None:
                self._calendar_cache.move_to_end(key)
                return cached
        dt = datetime.fromtimestamp(key * 60, UTC)
        hour_angle = 2.0 * math.pi * (dt.hour + dt.minute / 60.0) / 24.0
        dow_angle = 2.0 * math.pi * dt.weekday() / 7.0
        hours_to_close = self._calendar.get_hours_until_weekend_close(dt)
        weekly = self._calendar.trading_hours_per_week()
        hours_since_open = max(weekly - hours_to_close, 0.0) if self._calendar.is_trading_time(dt) else 0.0
        value = (
            math.sin(hour_angle),
            math.cos(hour_angle),
            math.sin(dow_angle),
            math.cos(dow_angle),
            hours_to_close / weekly if weekly > 0 else 0.0,
            1.0 if self._calendar.is_in_weekend_risk_window(dt) else 0.0,
            hours_to_close,
            hours_since_open,
        )
        with self._calendar_lock:
            self._calendar_cache[key] = value
            self._calendar_cache.move_to_end(key)
            while len(self._calendar_cache) > self._CALENDAR_CACHE_SIZE:
                self._calendar_cache.popitem(last=False)
        return value

    def _compute_factors(
        self,
        freq: TimesFMFreq,
        preset: TimesFMFactorPreset,
        arrays: dict[str, np.ndarray],
        basis: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """全因果地计算过去协变量矩阵 (F, T) 与截面因子矩阵 (T, 12)。"""
        bid = arrays["bid"]
        ask = arrays["ask"]
        count = bid.shape[0]
        step_1d, step_3d, step_1w = self._RETURN_STEPS[freq]
        ret_1d = self._shift_return(bid, step_1d)
        ret_3d = self._shift_return(bid, step_3d)
        ret_1w = self._shift_return(bid, step_1w)
        simple_ret = self._shift_return(bid, 1)
        log_ret = np.full(count, np.nan)
        if count > 1:
            log_ret[1:] = np.log(bid[1:] / bid[:-1])
        realized_vol = self._window_stat(simple_ret, self._VOL_WINDOW[freq], np.std) * self._VOL_SCALE[freq]
        atr = self._window_stat(bid, 20, np.std)
        mean20 = self._window_stat(bid, 20, np.mean)
        max20 = self._window_stat(bid, 20, np.max)
        min20 = self._window_stat(bid, 20, np.min)
        spread = ask - bid
        mid = (ask + bid) / 2.0
        spread_pct = spread / np.maximum(mid, 1e-12)
        named: dict[str, np.ndarray] = {
            "log_return_1": log_ret,
            "return_1d": ret_1d,
            "return_3d": ret_3d,
            "return_1w": ret_1w,
            "realized_volatility": realized_vol,
            "atr_pct": atr / np.maximum(bid, 1e-12),
            "spread_pct": spread_pct,
            "bank_basis": basis,
            "momentum_zscore": (bid - mean20) / np.maximum(atr, 1e-12),
            "range_pos_20": (bid - min20) / (max20 - min20 + 1e-12),
        }
        names = list(self.past_only_names(preset))
        if preset == "gold_ohlcv_multifactor":
            close = np.maximum(arrays["close"], 1e-12)
            named["hl_range_pct"] = (arrays["high"] - arrays["low"]) / close
            named["body_pct"] = (arrays["close"] - arrays["open"]) / np.maximum(arrays["open"], 1e-12)
            named["volume_log"] = np.log1p(np.maximum(arrays["volume"], 0.0))
        past_only = np.vstack([named[name] for name in names])
        past_only = np.nan_to_num(past_only, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
        calendar = arrays["calendar"]
        tabular_named = {
            "return_1d": ret_1d,
            "return_3d": ret_3d,
            "return_1w": ret_1w,
            "realized_volatility": realized_vol,
            "atr": atr,
            "spread": spread,
            "spread_pct": spread_pct,
            "bank_basis": basis,
            "hours_to_weekend_close": calendar[6],
            "hours_since_open": calendar[7],
            "is_weekend_risk_window": calendar[5],
            "event_risk_flag_count": np.zeros(count),
        }
        tabular = np.column_stack([tabular_named[name] for name in self.TABULAR_FACTORS]).astype(np.float64)
        tabular = np.where(np.isfinite(tabular), tabular, np.nan)
        return past_only, tabular

    @property
    def calendar(self) -> SessionCalendar:
        """返回数据集构建使用的交易时段日历实例。"""
        return self._calendar

    @classmethod
    def past_only_names(cls, preset: TimesFMFactorPreset) -> tuple[str, ...]:
        """返回过去协变量因子名称。"""
        if preset == "gold_ohlcv_multifactor":
            return cls.PAST_ONLY_BASE + cls.PAST_ONLY_OHLCV_EXTRA
        return cls.PAST_ONLY_BASE

    @classmethod
    def horizon_steps(cls, horizon: HoldingPeriod, freq: TimesFMFreq) -> int:
        """返回持有期在给定频率下的前瞻步数。"""
        return cls._STEPS[freq][horizon]

    def calendar_matrix(self, epochs: Sequence[float] | np.ndarray) -> np.ndarray:
        """返回给定时刻序列的 8 维日历特征矩阵 (8, N)。"""
        return np.array([self._calendar_tuple(float(epoch)) for epoch in epochs], dtype=np.float64).T.reshape(8, -1)

    def future_calendar(self, start_epoch: float, freq: TimesFMFreq, steps: int) -> np.ndarray:
        """返回决策时点之后 steps 个交易柱的确定性日历协变量 (6, steps)，自动跳过休市时段。"""
        delta = timedelta(seconds=self._FREQ_SECONDS[freq])
        cursor = datetime.fromtimestamp(float(start_epoch), UTC)
        future_epochs: list[float] = []
        for _ in range(steps):
            cursor = cursor + delta
            if freq == "1h":
                if not self._calendar.is_trading_time(cursor):
                    cursor = self._calendar.next_session_start(cursor).astimezone(UTC)
            else:
                while not self._calendar.is_trading_time(cursor):
                    cursor = cursor + timedelta(days=1)
            future_epochs.append(cursor.timestamp())
        matrix = self.calendar_matrix(future_epochs)
        return matrix[:6].astype(np.float32)

    def build_window(
        self,
        arrays: dict[str, np.ndarray],
        manifest: TimesFMDatasetManifest,
        index: int,
        steps: int,
        *,
        use_covariates: bool,
    ) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
        """构造序列位置 index 的因果上下文窗口及协变量（不含未来行情）。"""
        start = max(0, index - manifest.context_length + 1)
        context = arrays["bid"][start : index + 1].astype(np.float32)
        if not use_covariates:
            return context, None, None
        past_only = arrays["past_only"][:, start : index + 1]
        past_future = np.concatenate(
            [
                arrays["calendar"][:6, start : index + 1].astype(np.float32),
                self.future_calendar(float(arrays["times"][index]), manifest.freq, steps),
            ],
            axis=1,
        )
        return context, past_only, past_future

    def prepare_dataset(self, request: TimesFMPrepareRequest, *, target_dir: Path) -> TimesFMPrepareReport:
        """从本地行情构造多因子数据集并写入目录（拒绝覆盖已有数据集）。"""
        if (target_dir / self.MANIFEST_NAME).exists():
            raise TimesFMDatasetError("目标目录已存在 TimesFM 数据集，请使用新的目录")
        product_code = request.product_code or (
            self._default_product_code
            if request.source_mode == "quotes"
            else (XAUUSD_PRODUCT if request.source_mode == "kline" else "CMBC-JCJ")
        )
        horizons = list(dict.fromkeys(request.horizons))
        steps_map = {h.value: self.horizon_steps(h, request.freq) for h in horizons}
        max_steps = max(steps_map.values())
        context_length = request.context_length
        min_context = min(self.MIN_CONTEXT, context_length)

        raw_rows = self._load_rows(request, product_code)
        raw_rows.sort(key=operator.itemgetter(0))
        rows: list[_BarRow] = []
        for row in raw_rows:
            if rows and row[0] <= rows[-1][0]:
                continue
            if not all(math.isfinite(v) and v >= 0 for v in row[1:]) or row[6] <= 0 or row[7] < row[6]:
                continue
            rows.append(row)
        if len(rows) < min_context + max_steps + 1:
            needed = min_context + max_steps + 1
            raise TimesFMDatasetError(
                f"有效序列仅 {len(rows)} 条，不足以构造上下文与最大持有期前瞻（至少需要 {needed} 条）"
            )

        pit_times = [row[0] for row in rows]
        epochs = np.array([t.timestamp() for t in pit_times], dtype=np.float64)
        arrays: dict[str, np.ndarray] = {
            "times": epochs,
            "open": np.array([r[1] for r in rows], dtype=np.float64),
            "high": np.array([r[2] for r in rows], dtype=np.float64),
            "low": np.array([r[3] for r in rows], dtype=np.float64),
            "close": np.array([r[4] for r in rows], dtype=np.float64),
            "volume": np.array([r[5] for r in rows], dtype=np.float64),
            "bid": np.array([r[6] for r in rows], dtype=np.float64),
            "ask": np.array([r[7] for r in rows], dtype=np.float64),
        }
        arrays["calendar"] = self.calendar_matrix(epochs)
        if product_code == XAUUSD_PRODUCT:
            basis = np.full(len(rows), np.nan)
        else:
            basis_times, basis_prices = self._load_basis_series()
            refs = np.array([self._lookup_basis(basis_times, basis_prices, t) for t in pit_times], dtype=np.float64)
            mid = (arrays["bid"] + arrays["ask"]) / 2.0
            basis = np.where(np.isfinite(refs) & (refs > 0), mid / np.where(refs > 0, refs, 1.0) - 1.0, np.nan)
        arrays["past_only"], arrays["tabular"] = self._compute_factors(
            request.freq, request.factor_preset, arrays, basis
        )

        buy_mult = 1.0 + self._fee_rule.buy_fee_rate
        sell_mult = 1.0 - self._fee_rule.sell_fee_rate
        total = len(rows)
        sample_counts: dict[str, int] = {}
        for horizon in horizons:
            steps = steps_map[horizon.value]
            candidates = np.arange(min_context - 1, total - steps, request.stride, dtype=np.int64)
            exit_idx = candidates + steps
            labels = arrays["bid"][exit_idx] * sell_mult / (arrays["ask"][candidates] * buy_mult) - 1.0
            keep = np.isfinite(labels) & (labels > -1.0)
            candidates, exit_idx, labels = candidates[keep], exit_idx[keep], labels[keep]
            arrays[f"idx_{horizon.value}"] = candidates
            arrays[f"label_{horizon.value}"] = labels.astype(np.float64)
            arrays[f"label_end_{horizon.value}"] = epochs[exit_idx]
            sample_counts[horizon.value] = int(len(candidates))
        if not any(sample_counts.values()):
            raise TimesFMDatasetError("未生成任何有效样本")

        dataset_id = f"tfm_{product_code}_{request.freq}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:6]}"
        target_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(target_dir / self.SERIES_NAME, **cast(dict[str, Any], arrays))
        manifest = TimesFMDatasetManifest(
            dataset_id=dataset_id,
            dataset_dir=str(target_dir),
            source_mode=request.source_mode,
            product_code=product_code,
            freq=request.freq,
            factor_preset=request.factor_preset,
            context_length=context_length,
            stride=request.stride,
            horizons=horizons,
            horizon_steps=steps_map,
            total_bars=total,
            sample_counts=sample_counts,
            past_only_factors=list(self.past_only_names(request.factor_preset)),
            past_future_factors=list(self.FUTURE_FACTORS),
            tabular_factors=list(self.TABULAR_FACTORS),
            start_time=pit_times[0].isoformat(),
            end_time=pit_times[-1].isoformat(),
            spread_pct=request.spread_pct if request.source_mode != "quotes" else None,
            buy_fee_rate=self._fee_rule.buy_fee_rate,
            sell_fee_rate=self._fee_rule.sell_fee_rate,
            created_at=datetime.now(UTC),
        )
        (target_dir / self.MANIFEST_NAME).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return TimesFMPrepareReport(manifest=manifest, written_files=2)

    def load_dataset(self, dataset_dir: Path) -> tuple[TimesFMDatasetManifest, dict[str, np.ndarray]]:
        """读取数据集清单与全部序列数组。"""
        manifest = self.inspect_dataset(dataset_dir)
        series_path = dataset_dir / self.SERIES_NAME
        if manifest is None or not series_path.is_file():
            raise TimesFMDatasetError(f"TimesFM 数据集目录无效：{dataset_dir}")
        with np.load(series_path) as archive:
            return manifest, {key: archive[key] for key in archive.files}

    def inspect_dataset(self, dataset_dir: Path) -> TimesFMDatasetManifest | None:
        """读取数据集清单，目录无效或清单损坏时返回 None。"""
        path = dataset_dir / self.MANIFEST_NAME
        if not path.is_file():
            return None
        try:
            return TimesFMDatasetManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            return None
