"""多周期 K 线与分时行情后台定时采集服务。"""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from math import isfinite
from time import monotonic
from typing import ClassVar
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from src.clients.jd_gold import JDGoldError
from src.contracts.domains.market_bar import (
    XAUUSD_MERGED_SOURCE,
    XAUUSD_PRODUCT,
    KLineBar,
    MarketBarCollectorStatus,
    TimeSharingPoint,
)
from src.contracts.domains.reference_quote import ReferenceProductCatalog
from src.contracts.enums.jd_gold import IndexSymbol, KLineType, TimeSharingType
from src.contracts.exceptions import StorageError
from src.repositories.database import SQLiteDatabase
from src.repositories.market_bar import MarketBarRepository
from src.services.jd_gold.gateway import JDGoldGateway
from src.services.market.xauusd_merge import XauusdBarMerge


class MarketBarCollector:
    """定时采集公开多品种日/周/月 K 线与授权多周期 K 线、分时走势。"""

    _PUBLIC_KLINE_INTERVALS: ClassVar[frozenset[str]] = frozenset(
        {
            KLineType.DAY.value,
            KLineType.WEEK.value,
            KLineType.MONTH.value,
        }
    )
    _SLOW_INTERVAL_REFRESH_SECONDS: ClassVar[dict[str, float]] = {
        KLineType.DAY.value: 600.0,
        KLineType.WEEK.value: 1800.0,
        KLineType.MONTH.value: 1800.0,
        KLineType.YEAR.value: 1800.0,
    }
    _SIM_PRODUCT_CODE: ClassVar[str] = IndexSymbol.JDAU.value
    _SHANGHAI_TZ: ClassVar[ZoneInfo] = ZoneInfo("Asia/Shanghai")

    def __init__(
        self,
        gateway: JDGoldGateway,
        repository: MarketBarRepository,
        *,
        enabled: bool,
        interval_seconds: float,
        kline_products: tuple[str, ...],
        kline_intervals: tuple[str, ...],
        timesharing_intervals: tuple[str, ...],
    ) -> None:
        if not kline_products or len(set(kline_products)) != len(kline_products):
            raise ValueError("K 线采集品种列表不能为空或重复")
        for code in kline_products:
            ReferenceProductCatalog.get(code)
        if not kline_intervals or len(set(kline_intervals)) != len(kline_intervals):
            raise ValueError("K 线采集周期列表不能为空或重复")
        valid_klines = {item.value for item in KLineType}
        for interval in kline_intervals:
            if interval not in valid_klines:
                raise ValueError(f"不支持的 K 线周期：{interval}")
        if not timesharing_intervals or len(set(timesharing_intervals)) != len(timesharing_intervals):
            raise ValueError("分时采集颗粒度列表不能为空或重复")
        valid_ts = {item.value for item in TimeSharingType}
        for gran in timesharing_intervals:
            if gran not in valid_ts:
                raise ValueError(f"不支持的分时颗粒度：{gran}")

        self._gateway = gateway
        self._repository = repository
        self._enabled = enabled
        self._interval = interval_seconds
        self._kline_products = kline_products
        self._kline_intervals = kline_intervals
        self._timesharing_intervals = timesharing_intervals
        self._semaphore = asyncio.Semaphore(3)
        self._collect_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._last_task_success: dict[tuple[str, str, str], float] = {}
        self._state = MarketBarCollectorStatus(
            enabled=enabled,
            running=False,
            interval_seconds=interval_seconds,
            kline_bars_saved=0,
            timesharing_points_saved=0,
            failures=0,
            last_attempt_at=None,
            last_success_at=None,
            last_error=None,
            auth_available=False,
        )

    @staticmethod
    def _to_float(value: object) -> float | None:
        if value is None or isinstance(value, bool) or value in ("", "--"):
            return None
        if isinstance(value, (int, float)):
            parsed = float(value)
        elif isinstance(value, str):
            try:
                parsed = float(value)
            except ValueError:
                return None
        else:
            return None
        return parsed if isfinite(parsed) else None

    @classmethod
    def _parse_datetime(cls, value: object) -> datetime | None:
        if value is None or isinstance(value, bool) or value in ("", "--"):
            return None
        if isinstance(value, (int, str)):
            digits = str(value).strip()
            if digits.isdigit() and len(digits) in (10, 13):
                try:
                    return datetime.fromtimestamp(int(digits) / (1000 if len(digits) == 13 else 1), UTC)
                except (ValueError, OverflowError, OSError):
                    return None
        if isinstance(value, str):
            text = value.strip()
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
            return parsed.replace(tzinfo=cls._SHANGHAI_TZ) if parsed.tzinfo is None else parsed
        return None

    @classmethod
    def _parse_public_kline(
        cls,
        payload: object,
        *,
        product_code: str,
        interval: str,
        collected_at: datetime,
    ) -> list[KLineBar]:
        if not isinstance(payload, dict):
            raise ValueError("公开 K 线响应结构无效")
        raw_list = payload.get("kLineDtoList")
        if raw_list is None:
            return []
        if not isinstance(raw_list, list):
            raise ValueError("公开 K 线列表字段无效")
        bars: list[KLineBar] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            raw_date = item.get("date")
            bar_time = cls._parse_datetime(raw_date)
            open_p = cls._to_float(item.get("open"))
            high_p = cls._to_float(item.get("high"))
            low_p = cls._to_float(item.get("low"))
            close_p = cls._to_float(item.get("close"))
            if bar_time is None or open_p is None or high_p is None or low_p is None or close_p is None:
                continue
            raw_date_str = str(raw_date).strip() if isinstance(raw_date, str) else ""
            if len(raw_date_str) >= 10 and raw_date_str[4:5] == "-" and raw_date_str[7:8] == "-":
                trade_date = raw_date_str[:10]
            else:
                trade_date = bar_time.astimezone(cls._SHANGHAI_TZ).strftime("%Y-%m-%d")
            try:
                bars.append(
                    KLineBar(
                        product_code=product_code,
                        interval=interval,
                        source="jd_public_kline",
                        bar_time=bar_time,
                        trade_date=trade_date,
                        open_price=open_p,
                        high_price=high_p,
                        low_price=low_p,
                        close_price=close_p,
                        volume=cls._to_float(item.get("volume")),
                        collected_at=collected_at,
                    )
                )
            except ValidationError:
                continue
        return bars

    @classmethod
    def _parse_sim_kline(
        cls,
        payload: object,
        *,
        product_code: str,
        interval: str,
        collected_at: datetime,
    ) -> list[KLineBar]:
        if not isinstance(payload, dict):
            raise ValueError("模拟 K 线响应结构无效")
        raw_list = payload.get("items")
        if raw_list is None:
            return []
        if not isinstance(raw_list, list):
            raise ValueError("模拟 K 线列表字段无效")
        bars: list[KLineBar] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            bar_time = cls._parse_datetime(item.get("tradeTime") or item.get("tradeDate"))
            open_p = cls._to_float(item.get("openPrice"))
            high_p = cls._to_float(item.get("highPrice"))
            low_p = cls._to_float(item.get("lowPrice"))
            close_p = cls._to_float(item.get("closePrice"))
            if bar_time is None or open_p is None or high_p is None or low_p is None or close_p is None:
                continue
            raw_trade_date = item.get("tradeDate")
            trade_date = (
                str(raw_trade_date).strip()[:10]
                if isinstance(raw_trade_date, str) and raw_trade_date.strip()
                else bar_time.astimezone(cls._SHANGHAI_TZ).strftime("%Y-%m-%d")
            )
            try:
                bars.append(
                    KLineBar(
                        product_code=product_code,
                        interval=interval,
                        source="jd_sim_kline",
                        bar_time=bar_time,
                        trade_date=trade_date,
                        open_price=open_p,
                        high_price=high_p,
                        low_price=low_p,
                        close_price=close_p,
                        pre_close=cls._to_float(item.get("preClose")),
                        volume=cls._to_float(item.get("volume")),
                        turnover=cls._to_float(item.get("turnover")),
                        collected_at=collected_at,
                    )
                )
            except ValidationError:
                continue
        return bars

    @classmethod
    def _parse_sim_time_sharing(
        cls,
        payload: object,
        *,
        product_code: str,
        granularity: str,
        collected_at: datetime,
    ) -> list[TimeSharingPoint]:
        if not isinstance(payload, dict):
            raise ValueError("分时走势响应结构无效")
        raw_list = payload.get("items")
        if raw_list is None:
            return []
        if not isinstance(raw_list, list):
            raise ValueError("分时走势列表字段无效")
        points: list[TimeSharingPoint] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            trade_time = cls._parse_datetime(item.get("tradeTime"))
            last_price = cls._to_float(item.get("lastPrice"))
            if trade_time is None or last_price is None:
                continue
            raw_show = item.get("showTime")
            show_time = (
                raw_show.strip()
                if isinstance(raw_show, str) and raw_show.strip()
                else trade_time.astimezone(cls._SHANGHAI_TZ).strftime("%H:%M")
            )
            try:
                points.append(
                    TimeSharingPoint(
                        product_code=product_code,
                        granularity=granularity,
                        source="jd_sim_time_sharing",
                        trade_time=trade_time,
                        show_time=show_time,
                        last_price=last_price,
                        pre_close=cls._to_float(item.get("preClose")),
                        change_percent=cls._to_float(item.get("changePercent")),
                        trade_volume=cls._to_float(item.get("tradeVolume")),
                        total_volume=cls._to_float(item.get("totalVolume")),
                        turnover=cls._to_float(item.get("turnover")),
                        collected_at=collected_at,
                    )
                )
            except ValidationError:
                continue
        return points

    async def _save_klines_async(self, bars: list[KLineBar]) -> int:
        if not bars:
            return 0
        return await SQLiteDatabase.run_shielded_write(self._repository.save_klines, bars)

    async def _save_time_sharing_async(self, points: list[TimeSharingPoint]) -> int:
        if not points:
            return 0
        return await SQLiteDatabase.run_shielded_write(self._repository.save_time_sharing, points)

    def _is_due(self, task_key: tuple[str, str, str], interval: str, *, force: bool, now_mono: float) -> bool:
        """判断指定采集任务是否到达刷新周期；慢变周期（日/周/月/年）按更长间隔分频采集。"""
        if force:
            return True
        min_refresh = self._SLOW_INTERVAL_REFRESH_SECONDS.get(interval, 0.0)
        if min_refresh <= 0:
            return True
        last_ok = self._last_task_success.get(task_key)
        return last_ok is None or (now_mono - last_ok) >= min_refresh

    async def _collect_public_kline(
        self, product_code: str, interval: str
    ) -> tuple[tuple[str, str, str], int, str | None]:
        task_key = ("jd_public_kline", product_code, interval)
        try:
            async with self._semaphore:
                payload = await self._gateway.market.get_stock_kline(product_code, k_type=interval)
            bars = self._parse_public_kline(
                payload,
                product_code=product_code,
                interval=interval,
                collected_at=datetime.now(UTC),
            )
            return task_key, await self._save_klines_async(bars), None
        except (JDGoldError, ValueError, OSError, StorageError) as exc:
            err = (
                str(exc)
                if isinstance(exc, JDGoldError)
                else ("公开 K 线字段校验失败" if isinstance(exc, ValueError) else "K 线存储失败")
            )
            return task_key, 0, f"{product_code}/{interval}: {err}"

    async def _collect_sim_kline(self, interval: str) -> tuple[tuple[str, str, str], int, str | None]:
        task_key = ("jd_sim_kline", self._SIM_PRODUCT_CODE, interval)
        try:
            async with self._semaphore:
                payload = await self._gateway.trade.get_sim_kline(self._SIM_PRODUCT_CODE, k_type=interval)
            bars = self._parse_sim_kline(
                payload,
                product_code=self._SIM_PRODUCT_CODE,
                interval=interval,
                collected_at=datetime.now(UTC),
            )
            return task_key, await self._save_klines_async(bars), None
        except (JDGoldError, ValueError, OSError, StorageError) as exc:
            err = (
                str(exc)
                if isinstance(exc, JDGoldError)
                else ("模拟 K 线字段校验失败" if isinstance(exc, ValueError) else "K 线存储失败")
            )
            return task_key, 0, f"{self._SIM_PRODUCT_CODE}/sim-{interval}: {err}"

    async def _collect_sim_time_sharing(self, granularity: str) -> tuple[tuple[str, str, str], int, str | None]:
        task_key = ("jd_sim_time_sharing", self._SIM_PRODUCT_CODE, granularity)
        try:
            async with self._semaphore:
                payload = await self._gateway.trade.get_sim_time_sharing(self._SIM_PRODUCT_CODE, type_=granularity)
            points = self._parse_sim_time_sharing(
                payload,
                product_code=self._SIM_PRODUCT_CODE,
                granularity=granularity,
                collected_at=datetime.now(UTC),
            )
            return task_key, await self._save_time_sharing_async(points), None
        except (JDGoldError, ValueError, OSError, StorageError) as exc:
            err = (
                str(exc)
                if isinstance(exc, JDGoldError)
                else ("分时走势字段校验失败" if isinstance(exc, ValueError) else "分时存储失败")
            )
            return task_key, 0, f"{self._SIM_PRODUCT_CODE}/ts-{granularity}: {err}"

    async def _poll(self) -> None:
        logger = logging.getLogger(__name__)
        while True:
            try:
                status = await self.collect_once(force=False)
                if status.last_error:
                    logger.warning("多周期 K 线/分时采集存在失败：%s", status.last_error)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("多周期 K 线/分时轮询发生未预期异常")
            await asyncio.sleep(self._interval)

    async def collect_once(self, *, force: bool = True) -> MarketBarCollectorStatus:
        """执行一轮多周期 K 线与分时数据采集，单任务失败不阻断其余任务。"""
        if not self._enabled:
            raise RuntimeError("多周期 K 线与分时采集未启用")
        async with self._collect_lock:
            self._state.last_attempt_at = datetime.now(UTC)
            now_mono = monotonic()
            auth_state = await self._gateway.auth.auth_state_async()
            auth_ok = auth_state.is_authorized()
            self._state.auth_available = auth_ok

            public_intervals = [iv for iv in self._kline_intervals if iv in self._PUBLIC_KLINE_INTERVALS]
            kline_tasks = [
                self._collect_public_kline(code, interval)
                for code in self._kline_products
                for interval in public_intervals
                if self._is_due(("jd_public_kline", code, interval), interval, force=force, now_mono=now_mono)
            ]
            if auth_ok:
                kline_tasks.extend(
                    self._collect_sim_kline(interval)
                    for interval in self._kline_intervals
                    if self._is_due(
                        ("jd_sim_kline", self._SIM_PRODUCT_CODE, interval), interval, force=force, now_mono=now_mono
                    )
                )
            kline_results = await asyncio.gather(*kline_tasks) if kline_tasks else []
            ts_tasks = [self._collect_sim_time_sharing(gran) for gran in self._timesharing_intervals] if auth_ok else []
            ts_results = await asyncio.gather(*ts_tasks) if ts_tasks else []

            errors: list[str] = []
            kline_saved = 0
            ts_saved = 0
            succeeded = 0
            done_mono = monotonic()
            for task_key, count, err in kline_results:
                if err is not None:
                    errors.append(err)
                else:
                    self._last_task_success[task_key] = done_mono
                    kline_saved += count
                    succeeded += 1
            for task_key, count, err in ts_results:
                if err is not None:
                    errors.append(err)
                else:
                    self._last_task_success[task_key] = done_mono
                    ts_saved += count
                    succeeded += 1

            self._state.kline_bars_saved += kline_saved
            self._state.timesharing_points_saved += ts_saved
            if errors:
                self._state.failures += len(errors)
                self._state.last_error = "; ".join(errors[:3])
            else:
                self._state.last_error = None
            if succeeded > 0:
                self._state.last_success_at = datetime.now(UTC)
            return self.status()

    def query_klines(
        self,
        product_code: str,
        interval: str,
        *,
        source: str | tuple[str, ...] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int | None = 500,
    ) -> list[KLineBar]:
        """查询多周期 K 线，XAUUSD 默认返回合并序列。"""
        ReferenceProductCatalog.get(product_code)
        if source == XAUUSD_MERGED_SOURCE or (source is None and product_code == XAUUSD_PRODUCT):
            if product_code != XAUUSD_PRODUCT:
                raise ValueError("合并行情仅支持 WG-XAUUSD")
            return XauusdBarMerge(self._repository).query(
                interval, start_time=start_time, end_time=end_time, limit=limit
            )
        return self._repository.query_klines(
            product_code,
            interval,
            source=source,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    def query_time_sharing(
        self,
        product_code: str = "WG-JDAU",
        granularity: str = "m1",
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1500,
    ) -> list[TimeSharingPoint]:
        """查询已持久化的分时走势序列。"""
        ReferenceProductCatalog.get(product_code)
        return self._repository.query_time_sharing(
            product_code,
            granularity,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    def status(self) -> MarketBarCollectorStatus:
        """返回多周期 K 线与分时采集器的当前运行状态。"""
        return self._state.model_copy(update={"running": self._task is not None and not self._task.done()})

    async def start(self) -> None:
        """启用时随应用启动轮询，重复启动不创建额外任务。"""
        if self._enabled and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._poll())

    async def stop(self) -> None:
        """取消轮询并等待退出。"""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
