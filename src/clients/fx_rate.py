"""实时 USD/CNY 汇率公开接口客户端。

用于将 USD/oz 计价的国际贵金属参考价按实时汇率换算为展示用 CNY/g（不改写原始报价单位与哈希）。
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Mapping
from datetime import UTC, datetime

import httpx


class FXRateClient:
    """带内存 TTL 缓存和双源降级的实时 USD/CNY 汇率查询客户端。"""

    _PRIMARY_URL: str = "https://open.er-api.com/v6/latest/USD"
    _FALLBACK_URL: str = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=CNY"

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        ttl_seconds: float = 300.0,
        primary_url: str | None = None,
        fallback_url: str | None = None,
    ) -> None:
        self._http = http
        self._ttl_seconds = max(10.0, ttl_seconds)
        self._primary_url = primary_url or self._PRIMARY_URL
        self._fallback_url = fallback_url or self._FALLBACK_URL
        self._cached_rate: float | None = None
        self._cached_at: float = 0.0
        self._observed_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._max_stale_seconds: float = 86400.0

    @staticmethod
    def _parse_cny_rate(payload: object) -> float | None:
        if not isinstance(payload, Mapping):
            return None
        rates = payload.get("rates")
        if not isinstance(rates, Mapping):
            return None
        raw = rates.get("CNY")
        if raw is None or isinstance(raw, bool):
            return None
        try:
            value = float(str(raw))
        except ValueError:
            return None
        if not math.isfinite(value) or not (1.0 <= value <= 20.0):
            return None
        return value

    def cached_usd_cny(self) -> float | None:
        """返回当前内存中缓存的最新 USD/CNY 汇率（超过最大有效期限或无缓存时返回 None）。"""
        if self._cached_rate is None:
            return None
        if (time.monotonic() - self._cached_at) > self._max_stale_seconds:
            return None
        return self._cached_rate

    @property
    def observed_at(self) -> datetime | None:
        """返回最近成功获取汇率的时间。"""
        return self._observed_at

    async def get_usd_cny(self, *, force_refresh: bool = False) -> float | None:
        """获取实时 USD/CNY 汇率；命中 TTL 缓存直接返回，并发刷新单飞化，双源均失败时仅在最大有效期内回退旧缓存。"""
        now = time.monotonic()
        if not force_refresh and self._cached_rate is not None and (now - self._cached_at) < self._ttl_seconds:
            return self._cached_rate

        async with self._lock:
            now = time.monotonic()
            if not force_refresh and self._cached_rate is not None and (now - self._cached_at) < self._ttl_seconds:
                return self._cached_rate

            logger = logging.getLogger(__name__)
            for url in (self._primary_url, self._fallback_url):
                try:
                    resp = await self._http.get(url)
                    resp.raise_for_status()
                    rate = self._parse_cny_rate(resp.json())
                    if rate is not None:
                        self._cached_rate = rate
                        self._cached_at = time.monotonic()
                        self._observed_at = datetime.now(UTC)
                        return rate
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning("实时汇率接口请求失败（%s）：%s", url, exc)

            return self.cached_usd_cny()
