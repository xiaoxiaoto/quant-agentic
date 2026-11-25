"""京东多品种参考行情轮询、去重与质量分类。"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, FiniteFloat, StrictBool

from src.clients.fx_rate import FXRateClient
from src.clients.jd_gold import JDGoldClient, JDGoldError
from src.contracts.domains.reference_quote import LiveQuoteStatus, ReferenceProductCatalog, ReferenceQuote
from src.contracts.enums.trading import DataQualityState
from src.repositories.reference_quote import ReferenceQuoteRepository


class _JDPrice(BaseModel):
    """民生专用入口严格校验产品、演示标记与源时间。"""

    id: int = Field(gt=0)
    product_sku: Literal["21001001000001"] = Field(alias="productSku")
    demode: StrictBool
    price: FiniteFloat = Field(gt=0)
    time: str = Field(pattern=r"^\d{13}$")


class _MarketPrice(BaseModel):
    """外部价格必须是有限正数，布尔值不得作为价格。"""

    price: FiniteFloat = Field(gt=0)


class LiveQuoteCollector:
    """按产品隔离最新值与状态；单进程内限制并发并串行写入。"""

    _GRAMS_PER_TROY_OUNCE: float = 31.1034768

    def __init__(
        self,
        client: JDGoldClient,
        repository: ReferenceQuoteRepository,
        *,
        enabled: bool,
        interval_seconds: float,
        stale_seconds: float,
        product_codes: tuple[str, ...] | None = None,
        on_quote_saved: Callable[[ReferenceQuote], None] | None = None,
        fx_client: FXRateClient | None = None,
    ) -> None:
        self._repository = repository
        self._on_quote_saved = on_quote_saved
        self._fx_client = fx_client
        self._enabled = enabled
        self._interval = interval_seconds
        self._stale_seconds = stale_seconds
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._product_locks: dict[str, asyncio.Lock] = {
            product.code: asyncio.Lock() for product in ReferenceProductCatalog.all()
        }
        self._semaphore = asyncio.Semaphore(3)
        all_codes = tuple(p.code for p in ReferenceProductCatalog.all())
        self._latest = repository.latest_all(target_products=all_codes)
        self._product_codes = product_codes if product_codes is not None else all_codes
        if not self._product_codes or len(set(self._product_codes)) != len(self._product_codes):
            raise ValueError("采集品种列表不能为空或重复")
        for code in self._product_codes:
            ReferenceProductCatalog.get(code)
        self._states = {
            product.code: LiveQuoteStatus(
                product_code=product.code,
                enabled=enabled,
                running=False,
                interval_seconds=interval_seconds,
                records_saved=0,
                failures=0,
                last_attempt_at=None,
                last_success_at=None,
                last_error=None,
                latest=None,
            )
            for product in ReferenceProductCatalog.all()
        }
        self.client = client

    def _classify(self, quote: ReferenceQuote, now: datetime) -> ReferenceQuote:
        age = (now - quote.quote_at).total_seconds() if quote.quote_at is not None else None
        is_future_at_receipt = quote.quote_at is not None and (quote.received_at - quote.quote_at).total_seconds() < -5
        state = (
            DataQualityState.GAP
            if age is None
            else DataQualityState.ANOMALY
            if quote.quality_state == DataQualityState.ANOMALY or is_future_at_receipt or age < -5
            else DataQualityState.STALE
            if age > self._stale_seconds
            else DataQualityState.NORMAL
        )
        available_at = (quote.available_at or now) if state == DataQualityState.NORMAL else None
        updates: dict = {"quality_state": state, "available_at": available_at}
        if state != quote.quality_state or available_at != quote.available_at:
            # 读取时的时效视图不沿用原始日志哈希，日志本身保持不变。
            updates["record_hash"] = None
        return quote.model_copy(update=updates)

    async def _poll(self) -> None:
        logger = logging.getLogger(__name__)
        while True:
            try:
                statuses = await self.collect_all(self._product_codes)
                failed = [state.product_code for state in statuses if state.last_error]
                if failed:
                    logger.warning("京东参考行情采集失败品种：%s", ", ".join(failed))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("京东参考行情轮询发生未预期异常")
            await asyncio.sleep(self._interval)

    def _parse(self, payload: dict) -> ReferenceQuote:
        try:
            result = payload["resultData"]
            if result["status"] != "SUCCESS":
                raise ValueError("参考行情业务状态失败")
            data = _JDPrice.model_validate(result["datas"])
            if data.demode or isinstance(result["datas"].get("price"), bool):
                raise ValueError("不接受演示行情或布尔价格")
            quote_at = datetime.fromtimestamp(int(data.time) / 1000, UTC)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise ValueError("京东参考行情字段无效") from None
        now = datetime.now(UTC)
        return self._classify(
            ReferenceQuote(
                quote_id=f"jd-cmbc:{data.id}",
                price=data.price,
                quote_at=quote_at,
                received_at=now,
                payload_hash=self._hash(payload),
            ),
            now,
        )

    @staticmethod
    def _hash(payload: object) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _source_time(value: object) -> datetime | None:
        if value is None or value in ("", "{}", "--"):
            return None
        try:
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).isdigit():
                digits = str(value)
                if len(digits) not in (10, 13):
                    raise ValueError("无效源时间戳")
                return datetime.fromtimestamp(int(digits) / (1000 if len(digits) == 13 else 1), UTC)
            if isinstance(value, str):
                if "T" not in value and " " not in value:
                    raise ValueError("源时间缺少时分秒")
                parsed = datetime.fromisoformat(value)
                # 京东无偏移的交易时间按中国市场时区解释。
                return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai")) if parsed.tzinfo is None else parsed
        except (ValueError, OverflowError, OSError):
            raise ValueError("参考行情源时间无效") from None
        raise ValueError("参考行情源时间无效")

    def _parse_market(self, payload: object, code: str) -> ReferenceQuote:
        product = ReferenceProductCatalog.get(code)
        original = payload
        if isinstance(payload, list):
            matches = [item for item in payload if isinstance(item, dict) and item.get("uniqueCode") == code]
            if len(matches) != 1:
                raise ValueError("参考行情产品不匹配")
            payload = matches[0]
        if not isinstance(payload, dict):
            raise ValueError("参考行情结构无效")
        sge = product.category == "sge"
        identity = payload.get("stockCode") if sge else payload.get("uniqueCode")
        if identity not in ((code, code.removeprefix("SGE-")) if sge else (code,)):
            raise ValueError("参考行情产品不匹配")
        if payload.get("demode") not in (None, False, 0):
            raise ValueError("不接受演示行情")
        raw_price = payload.get("currentPrice") if sge else payload.get("lastPrice")
        # 严格拒绝布尔值价格，与民生专用路径的 StrictBool 校验对齐。
        if isinstance(raw_price, bool):
            raise ValueError("参考行情价格无效（布尔值）")
        price = _MarketPrice.model_validate({"price": raw_price}).price
        quote_at = self._source_time(payload.get("currentTime") if sge else payload.get("tradeDateTime"))
        now = datetime.now(UTC)
        identity_hash = self._hash({"price": price, "quote_at": quote_at.isoformat() if quote_at else None})
        return self._classify(
            ReferenceQuote(
                quote_id=f"jd:{code}:{identity_hash}",
                source_id="JD_PUBLIC_SGE" if sge else "JD_BFF_REFERENCE",
                product_code=code,
                unit=product.unit,
                price_side=product.price_side,
                price=price,
                quote_at=quote_at,
                received_at=now,
                payload_hash=self._hash(original),
            ),
            now,
        )

    async def _fetch(self, code: str) -> ReferenceQuote:
        product = ReferenceProductCatalog.get(code)
        if code == "CMBC-JCJ":
            return self._parse(await self.client.latest_price())
        if product.category == "sge":
            return self._parse_market(await self.client.stock_quote(code), code)
        return self._parse_market(await self.client.query_price(code), code)

    async def _collect_product(self, code: str) -> LiveQuoteStatus:
        try:
            await self.collect_once(code)
        except (JDGoldError, ValueError, OSError):
            pass
        return self.status(code)

    async def _save(self, quote: ReferenceQuote, state: LiveQuoteStatus) -> None:
        # 取消协程不能停止写盘线程，必须等写入及内存提交结束才释放锁。
        writing = asyncio.create_task(asyncio.to_thread(self._repository.save, quote))
        cancelled = False
        while not writing.done():
            try:
                await asyncio.shield(writing)
            except asyncio.CancelledError:
                cancelled = True
        writing.result()
        if self._on_quote_saved is not None:
            try:
                await asyncio.to_thread(self._on_quote_saved, quote)
            except Exception:
                logging.getLogger(__name__).exception("参考行情桥接回调执行失败：%s", quote.product_code)
        self._latest[quote.product_code] = quote
        state.records_saved += 1
        state.last_success_at = datetime.now(UTC)
        state.last_error = None
        if cancelled:
            raise asyncio.CancelledError

    def _compute_cny_per_g(self, latest: ReferenceQuote | None) -> tuple[float | None, float | None]:
        """将原始参考价按单位与实时汇率换算为展示用 CNY/g，不改写原始报价。"""
        if latest is None:
            return None, None
        if latest.unit == "CNY/g":
            return round(latest.price, 4), None
        if latest.unit == "CNY/kg":
            return round(latest.price / 1000.0, 4), None
        if latest.unit == "USD/oz":
            fx = self._fx_client.cached_usd_cny() if self._fx_client is not None else None
            if fx is None:
                return None, None
            return round(latest.price * fx / self._GRAMS_PER_TROY_OUNCE, 2), round(fx, 6)
        return None, None

    async def _refresh_fx_if_needed(self, product_codes: tuple[str, ...]) -> None:
        if self._fx_client is None:
            return
        if any(ReferenceProductCatalog.get(code).unit == "USD/oz" for code in product_codes):
            await self._fx_client.get_usd_cny()

    async def collect_once(self, product_code: str = "CMBC-JCJ") -> ReferenceQuote:
        """拉取指定品种；失败保留原记录，重复源记录不重复落盘。"""
        ReferenceProductCatalog.get(product_code)
        if not self._enabled:
            raise RuntimeError("实时行情采集未启用")
        await self._refresh_fx_if_needed((product_code,))
        # 同品种串行拉取与状态更新，避免并发乱序误报时间倒退；不同品种仍通过信号量并发。
        async with self._product_locks[product_code]:
            state = self._states[product_code]
            state.last_attempt_at = datetime.now(UTC)
            try:
                async with self._semaphore:
                    quote = await self._fetch(product_code)
                async with self._lock:
                    previous = self._latest.get(product_code)
                    if previous is not None:
                        if quote.quote_id == previous.quote_id:
                            if quote.price != previous.price or quote.quote_at != previous.quote_at:
                                raise ValueError("同一报价标识返回冲突内容")
                            state.last_success_at = datetime.now(UTC)
                            state.last_error = None
                            return self.latest(product_code) or previous
                        if (
                            previous.quote_at is not None
                            and quote.quote_at is not None
                            and DataQualityState.ANOMALY not in (previous.quality_state, quote.quality_state)
                            and quote.quote_at < previous.quote_at
                        ):
                            raise ValueError("参考行情源时间倒退")
                    await self._save(quote, state)
                    return quote
            except (JDGoldError, ValueError, OSError) as exc:
                state.failures += 1
                state.last_error = (
                    str(exc)
                    if isinstance(exc, JDGoldError)
                    else ("参考行情字段或时间校验失败" if isinstance(exc, ValueError) else "参考行情存储失败")
                )
                raise

    def latest(self, product_code: str = "CMBC-JCJ") -> ReferenceQuote | None:
        """按读取时刻重算指定品种的时效，缺少源时间始终标记缺口。"""
        ReferenceProductCatalog.get(product_code)
        quote = self._latest.get(product_code)
        return self._classify(quote, datetime.now(UTC)) if quote is not None else None

    def query_series(
        self,
        product_code: str = "CMBC-JCJ",
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[ReferenceQuote]:
        """按产品与时间区间查询已持久化的参考行情序列。"""
        ReferenceProductCatalog.get(product_code)
        return self._repository.query_series(
            product_code,
            start_time=start_time,
            end_time=end_time,
        )

    def status(self, product_code: str = "CMBC-JCJ") -> LiveQuoteStatus:
        """返回指定品种的独立采集状态，不包含鉴权配置。"""
        ReferenceProductCatalog.get(product_code)
        latest = self.latest(product_code)
        cny_per_g, fx_rate = self._compute_cny_per_g(latest)
        return self._states[product_code].model_copy(
            update={
                "running": self._task is not None and not self._task.done() and product_code in self._product_codes,
                "latest": latest,
                "cny_per_g": cny_per_g,
                "fx_rate_usd_cny": fx_rate,
            }
        )

    async def collect_all(self, product_codes: tuple[str, ...] | None = None) -> list[LiveQuoteStatus]:
        """有界并发采集，单个品种失败不会中断其他品种。"""
        if not self._enabled:
            raise RuntimeError("实时行情采集未启用")
        raw_codes = product_codes if product_codes is not None else tuple(p.code for p in ReferenceProductCatalog.all())
        codes = tuple(dict.fromkeys(raw_codes))
        for code in codes:
            ReferenceProductCatalog.get(code)
        await self._refresh_fx_if_needed(codes)
        return list(await asyncio.gather(*(self._collect_product(code) for code in codes)))

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
