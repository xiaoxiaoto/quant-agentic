"""行情报价 RESTful 接口路由。

提供行情记录录入、PIT 时间点最优报价查询及历史行情序列切片检索。
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from src.repositories.quote_repository import QuoteRepository
from src.types.domain.quote import QuoteRaw
from src.types.schemas import QuoteIngestRequest

router = APIRouter(prefix="/quotes", tags=["quotes"])
_repo = QuoteRepository()


@router.post("", response_model=QuoteRaw)
def record_quote(payload: QuoteIngestRequest) -> QuoteRaw:
    """录入单条行情报价并持久化至 PIT 数据湖。"""
    now_dt = datetime.now(UTC)
    quote = QuoteRaw(
        quote_id=payload.quote_id,
        source_id=payload.source_id,
        bid_price=payload.bid_price,
        ask_price=payload.ask_price,
        quote_at=payload.quote_at,
        received_at=now_dt,
        available_at=now_dt,
        is_tradable=payload.is_tradable,
        quality_state=payload.quality_state,
        product_code=payload.product_code,
    )
    _repo.save_quote(quote)
    return quote


@router.get("/latest", response_model=QuoteRaw)
def get_latest_quote(
    as_of: Annotated[datetime | None, Query(description="历史时间点截断（PIT 审计使用）")] = None,
    product_code: str = "GOLD_ACCUMULATION",
) -> QuoteRaw:
    """获取指定时间点满足 PIT 约束的最优最新行情。"""
    query_time = as_of or datetime.now(UTC)
    quote = _repo.get_latest_as_of(as_of=query_time, product_code=product_code)
    if not quote:
        raise HTTPException(status_code=404, detail="未找到符合 PIT 时间约束的有效行情")
    return quote
