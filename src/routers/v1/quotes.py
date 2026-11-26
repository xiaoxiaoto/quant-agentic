"""行情报价 RESTful 接口路由。

提供行情记录录入、PIT 时间点最优报价查询及历史行情序列切片检索。
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime

from src.clients.jd_gold import JDGoldError
from src.contracts.domains.market_bar import KLineBar, MarketBarCollectorStatus, TimeSharingPoint
from src.contracts.domains.quality import CollectorQualityReport
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.reference_quote import (
    LiveQuoteStatus,
    ReferenceProduct,
    ReferenceProductCatalog,
    ReferenceQuote,
)
from src.contracts.enums.trading import DataQualityState
from src.contracts.exceptions import StorageError
from src.contracts.schemas import QuoteIngestRequest
from src.runtime.dependencies import RuntimeDependencies

router = APIRouter(prefix="/quotes", tags=["quotes"])


def validate_live_product(product_code: str = "CMBC-JCJ") -> str:
    """在发起上游请求前拒绝未注册产品。"""
    try:
        ReferenceProductCatalog.get(product_code)
    except ValueError:
        raise HTTPException(status_code=422, detail="不支持的参考行情品种") from None
    return product_code


@router.get("/live/products", response_model=list[ReferenceProduct])
def list_live_products() -> list[ReferenceProduct]:
    """列出所有支持的品种、单位和价格方向。"""
    return list(ReferenceProductCatalog.all())


@router.get("/live/status/all", response_model=list[LiveQuoteStatus])
def get_all_live_statuses(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> list[LiveQuoteStatus]:
    """查看各品种的成功、失败及时间质量状态。"""
    return runtime.live_quotes.statuses()


@router.get("/live/latest/all", response_model=list[ReferenceQuote])
def get_all_live_quotes(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> list[ReferenceQuote]:
    """读取各品种最新已保存参考价，无记录的品种见状态列表。"""
    return [status.latest for status in runtime.live_quotes.statuses() if status.latest is not None]


@router.post("/live/collect/all", response_model=list[LiveQuoteStatus])
async def collect_all_live_quotes(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    product_codes: Annotated[
        list[str] | None,
        Query(description="指定采集的品种编码子集，留空则采集全部品种。"),
    ] = None,
) -> list[LiveQuoteStatus]:
    """立即采集指定品种（或全部品种），各自返回错误或参考值。"""
    if not runtime.settings.live_quotes_enabled:
        raise HTTPException(status_code=503, detail="实时行情采集未启用")
    if product_codes is not None:
        deduped = tuple(dict.fromkeys(product_codes))
        for code in deduped:
            try:
                ReferenceProductCatalog.get(code)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"不支持的参考行情品种：{code}") from None
        codes = deduped
    else:
        codes = None
    return await runtime.live_quotes.collect_all(codes)


@router.get("/live/status", response_model=LiveQuoteStatus)
def get_live_quote_status(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    product_code: Annotated[str, Depends(validate_live_product)],
) -> LiveQuoteStatus:
    """查询采集状态及参考行情时效。"""
    return runtime.live_quotes.status(product_code)


@router.get("/live/latest", response_model=ReferenceQuote)
def get_live_reference_quote(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    product_code: Annotated[str, Depends(validate_live_product)],
) -> ReferenceQuote:
    """读取已保存的参考行情，陈旧数据明确标记状态。"""
    quote = runtime.live_quotes.latest(product_code)
    if quote is None:
        raise HTTPException(status_code=404, detail="尚未采集到参考行情")
    return quote


@router.post("/live/collect", response_model=ReferenceQuote)
async def collect_live_reference_quote(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    product_code: Annotated[str, Depends(validate_live_product)],
) -> ReferenceQuote:
    """立即采集一次参考行情。"""
    if not runtime.settings.live_quotes_enabled:
        raise HTTPException(status_code=503, detail="实时行情采集未启用")
    try:
        return await runtime.live_quotes.collect_once(product_code)
    except (JDGoldError, ValueError):
        raise HTTPException(status_code=502, detail="京东参考行情暂不可用，详情见采集状态") from None
    except (OSError, StorageError):
        raise HTTPException(status_code=503, detail="参考行情存储暂不可用") from None


@router.get("/bars/status", response_model=MarketBarCollectorStatus)
def get_market_bars_status(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> MarketBarCollectorStatus:
    """查询多周期 K 线与分时采集器运行状态。"""
    return runtime.market_bars.status()


@router.post("/bars/collect", response_model=MarketBarCollectorStatus)
async def collect_market_bars(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> MarketBarCollectorStatus:
    """立即触发一轮多周期 K 线与分时数据采集。"""
    if not runtime.settings.market_bars_enabled:
        raise HTTPException(status_code=503, detail="多周期 K 线与分时采集未启用")
    return await runtime.market_bars.collect_once()


@router.get("/bars/kline", response_model=list[KLineBar])
def query_market_klines(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    product_code: Annotated[str, Depends(validate_live_product)],
    interval: Annotated[
        Literal["m1", "m5", "m15", "m30", "m60", "day", "week", "month", "year"],
        Query(description="K 线周期（m1/m5/m15/m30/m60/day/week/month/year）"),
    ] = "day",
    source: Annotated[
        Literal["jd_public_kline", "jd_sim_kline"] | None,
        Query(description="可选数据来源过滤（jd_public_kline / jd_sim_kline）"),
    ] = None,
    start_time: Annotated[AwareDatetime | None, Query(description="起始时间（带时区）")] = None,
    end_time: Annotated[AwareDatetime | None, Query(description="结束时间（带时区）")] = None,
    limit: Annotated[int, Query(ge=1, le=5000, description="返回最大条数")] = 500,
) -> list[KLineBar]:
    """查询 SQLite 中已采集的多周期 K 线历史序列。"""
    if start_time is not None and end_time is not None and start_time > end_time:
        raise HTTPException(status_code=400, detail="起始时间不得晚于结束时间")
    return runtime.market_bars.query_klines(
        product_code,
        interval,
        source=source,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )


@router.get("/bars/timesharing", response_model=list[TimeSharingPoint])
def query_market_timesharing(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    product_code: Annotated[str, Query(description="参考品种编码")] = "WG-JDAU",
    granularity: Annotated[
        Literal["m1", "m5"],
        Query(description="分时颗粒度（m1/m5）"),
    ] = "m1",
    start_time: Annotated[AwareDatetime | None, Query(description="起始时间（带时区）")] = None,
    end_time: Annotated[AwareDatetime | None, Query(description="结束时间（带时区）")] = None,
    limit: Annotated[int, Query(ge=1, le=5000, description="返回最大条数")] = 1500,
) -> list[TimeSharingPoint]:
    """查询 SQLite 中已采集的分时走势点位序列。"""
    validate_live_product(product_code)
    if start_time is not None and end_time is not None and start_time > end_time:
        raise HTTPException(status_code=400, detail="起始时间不得晚于结束时间")
    return runtime.market_bars.query_time_sharing(
        product_code,
        granularity,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )


@router.post("", response_model=QuoteRaw)
def record_quote(
    payload: QuoteIngestRequest, runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)]
) -> QuoteRaw:
    """录入单条行情报价并持久化至 PIT 数据湖。"""
    now_dt = datetime.now(UTC)
    quote = QuoteRaw(
        quote_id=payload.quote_id,
        source_id=payload.source_id,
        bid_price=payload.bid_price,
        ask_price=payload.ask_price,
        quote_at=payload.quote_at,
        received_at=now_dt,
        available_at=None,
        is_tradable=payload.is_tradable,
        quality_state=DataQualityState.NORMAL,
        product_code=payload.product_code,
    )
    quote.quality_state = runtime.quality_guard(payload.source_id, payload.product_code).validate_and_classify(
        quote, now=now_dt
    )
    if quote.quality_state == DataQualityState.NORMAL:
        quote.available_at = now_dt
    try:
        runtime.quotes.save_quote(quote)
    except OSError:
        raise HTTPException(status_code=503, detail="行情存储暂不可用") from None
    return quote


@router.get("/latest", response_model=QuoteRaw)
def get_latest_quote(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    as_of: Annotated[AwareDatetime | None, Query(description="历史时间点截断（PIT 审计使用）")] = None,
    product_code: str = "GOLD_ACCUMULATION",
) -> QuoteRaw:
    """获取指定时间点满足 PIT 约束的最优最新行情。"""
    query_time = as_of or datetime.now(UTC)
    quote = runtime.quotes.get_latest_as_of(as_of=query_time, product_code=product_code)
    if not quote:
        raise HTTPException(status_code=404, detail="未找到符合 PIT 时间约束的有效行情")
    return quote


@router.get("/quality", response_model=CollectorQualityReport)
def get_quote_quality_report(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    product_code: Annotated[str, Query(description="产品编码")] = "GOLD_ACCUMULATION",
    start_time: Annotated[AwareDatetime | None, Query(description="起始时间（带时区）")] = None,
    end_time: Annotated[AwareDatetime | None, Query(description="结束时间（带时区）")] = None,
) -> CollectorQualityReport:
    """统计指定时间窗口内的行情采样时延、缺口、点差与周末跳空质量报告。"""
    effective_end = end_time or datetime.now(UTC)
    effective_start = start_time or (effective_end - timedelta(days=30))
    if effective_start > effective_end:
        raise HTTPException(status_code=400, detail="起始时间不得晚于结束时间")
    series = runtime.quotes.query_pit_series(
        start_time=effective_start,
        end_time=effective_end,
        as_of=effective_end,
        product_code=product_code,
        valid_only=False,
    )
    return runtime.collector_quality.analyze(series)
