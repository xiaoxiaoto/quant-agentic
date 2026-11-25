"""行情报价 RESTful 接口路由。

提供行情记录录入、PIT 时间点最优报价查询及历史行情序列切片检索。
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime

from src.clients.jd_gold import JDGoldError
from src.contracts.domains.quality import CollectorQualityReport
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.reference_quote import (
    LiveQuoteStatus,
    ReferenceProduct,
    ReferenceProductCatalog,
    ReferenceQuote,
)
from src.contracts.enums.trading import DataQualityState
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
    return [runtime.live_quotes.status(product.code) for product in ReferenceProductCatalog.all()]


@router.get("/live/latest/all", response_model=list[ReferenceQuote])
def get_all_live_quotes(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> list[ReferenceQuote]:
    """读取各品种最新已保存参考价，无记录的品种见状态列表。"""
    return [
        quote
        for product in ReferenceProductCatalog.all()
        if (quote := runtime.live_quotes.latest(product.code)) is not None
    ]


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
    except OSError:
        raise HTTPException(status_code=503, detail="参考行情存储暂不可用") from None


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
