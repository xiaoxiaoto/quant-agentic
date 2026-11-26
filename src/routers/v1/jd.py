"""京东黄金账户查询、公开行情/K 线、资讯与智能分析网关 RESTful 接口路由。"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import JsonValue

from src.clients.jd_gold import JDGoldError, JDGoldLoginRequiredError
from src.contracts.domains.jd_auth import JDGoldBrowserRenewError
from src.contracts.domains.jd_collection import JDHoldingsSnapshot, JDNewsArticle
from src.contracts.schemas import (
    JDAuthActionResponse,
    JDAuthLoginResponse,
    JDAuthStatusResponse,
)
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


def _validate_date_range(start_date: str, end_date: str) -> None:
    """校验 YYYY-MM-DD 日期字符串为合法日历日期且起止顺序有效。"""
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="无效的日历日期（请使用有效的 YYYY-MM-DD）") from None
    if start_dt > end_dt:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")


def _raise_jd_http_error(exc: Exception) -> None:
    """将京东客户端异常统一转换为标准 HTTP 异常。"""
    if isinstance(exc, JDGoldLoginRequiredError):
        raise HTTPException(
            status_code=401,
            detail="京东账户尚未授权或登录态已过期，请先发起京东登录授权",
        ) from None
    if isinstance(exc, JDGoldError):
        if exc.code == "AUTH_REQUIRED":
            raise HTTPException(
                status_code=401,
                detail="京东账户尚未授权或登录态已过期，请先发起京东登录授权",
            ) from None
        raise HTTPException(status_code=502, detail=f"京东上游接口调用失败（{exc.code}）") from None
    raise exc


@router.get("/auth/status", response_model=JDAuthStatusResponse)
async def get_jd_auth_status(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    response: Response,
) -> JDAuthStatusResponse:
    """查询当前京东账户的授权状态与待确认登录会话信息。"""
    response.headers["Cache-Control"] = "no-store"
    return await runtime.jd_gold.auth.status_summary()


@router.post("/auth/login", response_model=JDAuthLoginResponse)
async def start_jd_auth_login(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    response: Response,
) -> JDAuthLoginResponse:
    """发起京东浏览器授权登录，返回京东官方授权链接。"""
    response.headers["Cache-Control"] = "no-store"
    try:
        authorize_url = await runtime.jd_gold.login()
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise
    status = await runtime.jd_gold.auth.status_summary()
    return JDAuthLoginResponse(
        authorize_url=authorize_url.get_secret_value(),
        expires_at=status.pending_login_expires_at,
        message="已生成京东授权链接，请在新页面完成登录后点击“我已完成授权”",
    )


@router.post("/auth/complete", response_model=JDAuthActionResponse)
async def complete_jd_auth_login(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    response: Response,
) -> JDAuthActionResponse:
    """在用户完成浏览器授权后换取正式令牌，并立即尝试同步一次持仓快照。"""
    response.headers["Cache-Control"] = "no-store"
    try:
        await runtime.jd_gold.complete_login()
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise
    status = await runtime.refresh_jd_after_auth("京东授权完成")
    return JDAuthActionResponse(
        ok=True,
        message="京东登录成功，登录态已保存",
        status=status,
    )


@router.post("/auth/renew", response_model=JDAuthActionResponse)
async def renew_jd_auth(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    response: Response,
) -> JDAuthActionResponse:
    """立即通过浏览器自动化执行京东账号续约，并尝试同步持仓快照。"""
    response.headers["Cache-Control"] = "no-store"
    try:
        await runtime.jd_gold.auto_renew()
    except JDGoldBrowserRenewError as exc:
        if exc.code in {"CAPTCHA_OR_MFA_REQUIRED", "MANUAL_LOGIN_REQUIRED"}:
            raise HTTPException(status_code=409, detail=exc.message) from None
        if exc.code == "RENEW_TIMEOUT":
            raise HTTPException(status_code=504, detail=exc.message) from None
        if exc.code in {
            "CDP_UNAVAILABLE",
            "PLAYWRIGHT_UNAVAILABLE",
            "BROWSER_LAUNCH_FAILED",
            "PROFILE_NOT_CONFIGURED",
            "AUTO_RENEW_NOT_CONFIGURED",
        }:
            raise HTTPException(status_code=503, detail=exc.message) from None
        raise HTTPException(status_code=502, detail=exc.message) from None
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise
    status = await runtime.refresh_jd_after_auth("京东自动续约完成")
    return JDAuthActionResponse(
        ok=True,
        message="京东账号浏览器自动续约成功，登录态已更新",
        status=status,
    )


@router.post("/auth/verify", response_model=JDAuthActionResponse)
async def verify_jd_auth(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    response: Response,
) -> JDAuthActionResponse:
    """实时调用京东账户接口验证当前登录态是否有效。"""
    response.headers["Cache-Control"] = "no-store"
    try:
        await runtime.jd_gold.verify_auth()
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise
    status = await runtime.jd_gold.auth.status_summary()
    return JDAuthActionResponse(
        ok=True,
        message="京东账户权限验证通过",
        status=status,
    )


@router.post("/auth/logout", response_model=JDAuthActionResponse)
async def logout_jd_auth(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    response: Response,
) -> JDAuthActionResponse:
    """退出当前京东账户登录并清除本地持久化凭据。"""
    response.headers["Cache-Control"] = "no-store"
    await runtime.jd_gold.logout()
    status = await runtime.jd_gold.auth.status_summary()
    return JDAuthActionResponse(
        ok=True,
        message="已退出京东登录并清除本地授权凭据",
        status=status,
    )


@router.get("/holdings")
def get_jd_holdings(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    response: Response,
) -> dict[str, JsonValue]:
    """从 SQLite 查询当前授权下最近成功的京东持仓。"""
    response.headers["Cache-Control"] = "no-store"
    snapshot = runtime.jd_holdings.latest()
    if snapshot.data is not None:
        return snapshot.data
    if snapshot.status == "auth_required":
        raise HTTPException(status_code=401, detail="京东账户尚未授权")
    raise HTTPException(status_code=404, detail="尚未采集到京东持仓")


@router.get("/holdings/snapshot")
def get_jd_holdings_snapshot(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    response: Response,
) -> JDHoldingsSnapshot:
    """读取持仓快照及本地采集时效。"""
    response.headers["Cache-Control"] = "no-store"
    return runtime.jd_holdings.latest()


@router.get("/orders")
async def get_jd_orders(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    response: Response,
    start_date: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$", description="开始日期（YYYY-MM-DD）")],
    end_date: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$", description="结束日期（YYYY-MM-DD）")],
    page_no: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, description="每页条数")] = 50,
) -> JsonValue:
    """查询京东实盘历史订单列表。"""
    response.headers["Cache-Control"] = "no-store"
    _validate_date_range(start_date, end_date)
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
    response: Response,
    start_date: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$", description="开始日期（YYYY-MM-DD）")],
    end_date: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$", description="结束日期（YYYY-MM-DD）")],
) -> JsonValue:
    """查询京东实盘订单汇总统计。"""
    response.headers["Cache-Control"] = "no-store"
    _validate_date_range(start_date, end_date)
    try:
        return await runtime.live.fetch_jd_order_summary(start_date=start_date, end_date=end_date)
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/income-calendar")
async def get_jd_income_calendar(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    response: Response,
    time_type: Annotated[Literal["DAY", "MONTH", "YEAR"], Query(description="时间粒度（DAY/MONTH/YEAR）")] = "DAY",
    time_value: Annotated[str, Query(max_length=32, description="指定时间值")] = "",
    trade_type: Annotated[str, Query(min_length=1, max_length=32, description="交易类型过滤")] = "ALL",
) -> JsonValue:
    """查询京东实盘收益日历。"""
    response.headers["Cache-Control"] = "no-store"
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
    response: Response,
    start_date: Annotated[str, Query(description="开始时间（YYYY-MM-DD 或 ISO-8601）")],
    end_date: Annotated[str, Query(description="结束时间（YYYY-MM-DD 或 ISO-8601）")],
    bank: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{2,16}$", description="银行代码")] = "CZB",
    page_index: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 10,
) -> JsonValue:
    """查询京东实盘条件挂单列表（自动转换毫秒时间戳）。"""
    response.headers["Cache-Control"] = "no-store"
    start_ms = _date_or_iso_to_ms(start_date, end_of_day=False)
    end_ms = _date_or_iso_to_ms(end_date, end_of_day=True)
    if start_ms > end_ms:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
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
    symbol: Annotated[str, Query(min_length=1, max_length=64, description="贵金属现货合约编码")] = "SGE-Au99.99",
    k_type: Annotated[
        Literal["day", "week", "month", "m1", "m5", "m15", "m30", "m60"],
        Query(description="K 线周期（day/week/month/m1/m5/m15/m30/m60）"),
    ] = "day",
) -> JsonValue:
    """查询贵金属现货 K 线行情数据。"""
    try:
        return await runtime.jd_gold.market.get_stock_kline(symbol, k_type=k_type)
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise


@router.get("/news")
def get_jd_news(
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
    channel: Annotated[Literal["news", "flash", "morning"], Query(description="资讯频道")] = "news",
    query: Annotated[str | None, Query(max_length=128, description="资讯检索关键词")] = None,
    size: Annotated[int, Query(ge=1, le=100, description="返回条数")] = 10,
) -> list[JDNewsArticle]:
    """查询 SQLite 中已采集的京东资讯、快讯或早报。"""
    return runtime.news_sync.list_articles(limit=size, channel=channel, query=query)


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
    rank_type: Annotated[str, Query(pattern=r"^[A-Z0-9_]{2,32}$", description="排行维度")] = "TOTAL_RETURN",
) -> JsonValue:
    """查询黄金达人与策略排行。"""
    try:
        return await runtime.jd_gold.analysis.get_gold_ranking(rank_type=rank_type)
    except (JDGoldLoginRequiredError, JDGoldError) as exc:
        _raise_jd_http_error(exc)
        raise
