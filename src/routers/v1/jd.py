"""京东黄金账户查询、公开行情/K 线、资讯与智能分析网关 RESTful 接口路由。"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import JsonValue

from src.clients.jd_gold import JDGoldError, JDGoldLoginRequiredError
from src.runtime.dependencies import RuntimeDependencies

router = APIRouter(prefix="/jd", tags=["jd"])


def _date_or_iso_to_ms(raw: str, *, end_of_day: bool = False) -> int:
    """将 YYYY-MM-DD 或 ISO-8601 时间字符串转换为毫秒时间戳。"""
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            parsed = datetime.fromisoformat(f"{raw}T23:59:59.999000+00:00" if end_of_day else f"{raw}T00:00:00+00:00")
        else:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"无效的时间格式（请使用 YYYY-MM-DD 或 ISO-8601）：{raw}") from None
    return int(parsed.timestamp() * 1000)


def _raise_jd_http_error(exc: Exception) -> None:
    """将京东客户端异常统一转换为标准 HTTP 异常。"""
    if isinstance(exc, JDGoldLoginRequiredError):
        raise HTTPException(
            status_code=401,
            detail="京东账户尚未授权或登录态已过期，请先运行 auth login 完成授权",
        ) from None
    if isinstance(exc, JDGoldError):
        raise HTTPException(status_code=502, detail=f"京东上游接口调用失败（{exc.code}）") from None
    raise exc


@router.get("/holdings")
async def get_jd_holdings(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> JsonValue:
    """查询京东实盘账户黄金持仓明细。"""
    try:
        return await runtime.live.fetch_jd_holdings()
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/orders")
async def get_jd_orders(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    start_date: Annotated[str, Query(description="开始日期（YYYY-MM-DD）")],
    end_date: Annotated[str, Query(description="结束日期（YYYY-MM-DD）")],
    page_no: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, description="每页条数")] = 50,
) -> JsonValue:
    """查询京东实盘历史订单列表。"""
    try:
        return await runtime.live.fetch_jd_orders(
            start_date=start_date,
            end_date=end_date,
            page_no=page_no,
            page_size=page_size,
        )
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/orders/summary")
async def get_jd_order_summary(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    start_date: Annotated[str, Query(description="开始日期（YYYY-MM-DD）")],
    end_date: Annotated[str, Query(description="结束日期（YYYY-MM-DD）")],
) -> JsonValue:
    """查询京东实盘订单汇总统计。"""
    try:
        return await runtime.live.fetch_jd_order_summary(start_date=start_date, end_date=end_date)
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/income-calendar")
async def get_jd_income_calendar(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    time_type: Annotated[str, Query(description="时间粒度（DAY/MONTH/YEAR）")] = "DAY",
    time_value: Annotated[str, Query(description="指定时间值")] = "",
    trade_type: Annotated[str, Query(description="交易类型过滤")] = "ALL",
) -> JsonValue:
    """查询京东实盘收益日历。"""
    try:
        return await runtime.live.fetch_jd_income_calendar(
            time_type=time_type,
            time_value=time_value,
            trade_type=trade_type,
        )
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/conditional-orders")
async def get_jd_conditional_orders(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    start_date: Annotated[str, Query(description="开始时间（YYYY-MM-DD 或 ISO-8601）")],
    end_date: Annotated[str, Query(description="结束时间（YYYY-MM-DD 或 ISO-8601）")],
    bank: Annotated[str, Query(description="银行代码")] = "CZB",
    page_index: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 10,
) -> JsonValue:
    """查询京东实盘条件挂单列表（自动转换毫秒时间戳）。"""
    start_ms = _date_or_iso_to_ms(start_date, end_of_day=False)
    end_ms = _date_or_iso_to_ms(end_date, end_of_day=True)
    try:
        return await runtime.live.fetch_jd_conditional_orders(
            bank=bank,
            start_time_ms=start_ms,
            end_time_ms=end_ms,
            page_index=page_index,
            page_size=page_size,
        )
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/market/kline")
async def get_jd_kline(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    symbol: Annotated[str, Query(description="贵金属现货合约编码")] = "SGE-Au99.99",
    k_type: Annotated[str, Query(description="K 线周期（day/week/month/m1/m5/m15/m30/m60）")] = "day",
) -> JsonValue:
    """查询贵金属现货 K 线行情数据。"""
    try:
        return await runtime.jd_gold.market.get_stock_kline(symbol, k_type=k_type)
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/news")
async def get_jd_news(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    channel: Annotated[Literal["news", "flash", "morning"], Query(description="资讯频道")] = "news",
    query: Annotated[str, Query(description="资讯检索关键词")] = "黄金",
    size: Annotated[int, Query(ge=1, le=100, description="返回条数")] = 10,
) -> JsonValue:
    """抓取京东黄金资讯、快讯或早报。"""
    try:
        if channel == "news":
            return await runtime.jd_gold.market.get_news(query=query, size=size)
        if channel == "flash":
            return await runtime.jd_gold.market.get_news_flash(page_size=size)
        return await runtime.jd_gold.market.get_morning_report(page_size=size)
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/analysis/opportunities")
async def get_jd_opportunities(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> JsonValue:
    """查询黄金套利与波段机会分析。"""
    try:
        return await runtime.jd_gold.analysis.get_opportunities()
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/analysis/orderbook")
async def get_jd_orderbook(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> JsonValue:
    """查询黄金盘口挂单分布。"""
    try:
        return await runtime.jd_gold.analysis.get_order_book()
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/analysis/resonance")
async def get_jd_resonance(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> JsonValue:
    """查询多指标共振信号分析。"""
    try:
        return await runtime.jd_gold.analysis.get_indicator_resonance()
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/analysis/bomb")
async def get_jd_bomb(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    details: Annotated[bool, Query(description="是否返回大单异动明细列表")] = False,
) -> JsonValue:
    """查询突发大单异动指标或明细。"""
    try:
        if details:
            return await runtime.jd_gold.analysis.get_bomb_details()
        return await runtime.jd_gold.analysis.get_bomb_indicators()
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/analysis/ranking")
async def get_jd_ranking(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    rank_type: Annotated[str, Query(description="排行维度")] = "TOTAL_RETURN",
) -> JsonValue:
    """查询黄金达人与策略排行。"""
    try:
        return await runtime.jd_gold.analysis.get_gold_ranking(rank_type=rank_type)
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise
