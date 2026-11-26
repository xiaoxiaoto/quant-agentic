"""京东黄金业务接口；连接生命周期由调用方管理。"""

import asyncio
import base64
import hashlib
import math
import re
import secrets
import time
from uuid import uuid4

import httpx
from pydantic import (
    HttpUrl,
    JsonValue,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    SecretStr,
)

from src.contracts.domains.jd_auth import JD_GOLD_REAUTH_LEAD_SECONDS, JDGoldAuthState, JDGoldAuthStore


class JDGoldError(RuntimeError):
    """不包含上游响应或凭据的接口异常。"""

    code: str

    def __init__(self, code: str, message: str | None = None) -> None:
        """初始化接口异常，非白名单或格式不符合规则的状态码将统一遮蔽。"""
        safe_codes = {
            "UPSTREAM_ERROR",
            "AUTH_REQUIRED",
            "API_KEY_REQUIRED",
            "INVALID_ENVELOPE",
            "INVALID_JSON",
            "NETWORK_ERROR",
        }
        self.code = code if code in safe_codes or re.fullmatch(r"-?[0-9]{1,12}", code) else "UPSTREAM_ERROR"
        super().__init__(message or f"京东黄金接口调用失败（{self.code}）")


class JDGoldLoginRequiredError(JDGoldError):
    """需要用户授权；链接仅供显式展示，不进入异常消息。"""

    def __init__(self, authorize_url: SecretStr) -> None:
        """保存授权链接并保持异常文本脱敏。"""
        super().__init__("AUTH_REQUIRED", "请先完成京东登录授权，再调用 complete_login")
        self.authorize_url = authorize_url


class JDGoldClient:
    """统一封装行情、账户、授权与显式模拟交易请求。"""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        bff_base_url: HttpUrl | str = "https://ms.jr.jd.com/gw2/generic/cfGoldAi/pc/m",
        public_base_url: HttpUrl | str = "https://ms.jr.jd.com/gw2/generic/ugActs/h5/m",
        gateway_base_url: HttpUrl | str = "https://youqian.jd.com/api/gateway",
        latest_price_url: HttpUrl | str = "https://api.jdjygold.com/gw/generic/hj/h5/m/latestPrice",
        access_token: SecretStr | str = "",
        gateway_api_key: SecretStr | str = "",
        claw: str = "",
        auth_store: JDGoldAuthStore | None = None,
    ) -> None:
        """初始化京东黄金业务客户端。"""
        self._http = http
        self._bff_base_url = str(bff_base_url).rstrip("/")
        self._public_base_url = str(public_base_url).rstrip("/")
        self._gateway_base_url = str(gateway_base_url).rstrip("/")
        self._latest_price_url = str(latest_price_url)
        self._access_token = access_token if isinstance(access_token, SecretStr) else SecretStr(access_token)
        self._gateway_api_key = (
            gateway_api_key if isinstance(gateway_api_key, SecretStr) else SecretStr(gateway_api_key)
        )
        self._claw = claw
        self._token_deadline: float | None = None
        self._expired_token_fingerprint: str | None = None
        self._login_session: tuple[str, SecretStr, SecretStr, float] | None = None
        self._login_lock = asyncio.Lock()
        self._auth_store = auth_store
        configured_token = self._access_token
        self._restore_auth()
        if configured_token.get_secret_value():
            self._access_token = configured_token
            self._expired_token_fingerprint = None
            self._token_deadline = None
            self._persist_auth()

    def _apply_auth_state(self, state: JDGoldAuthState) -> None:
        """将持久化的授权状态同步到内存字段。"""
        self._access_token = state.access_token
        self._token_deadline = state.expires_at
        self._expired_token_fingerprint = state.expired_token_fingerprint
        self._login_session = (
            (state.login_state, state.login_verifier, state.login_url, state.login_expires_at)
            if state.login_state
            else None
        )

    def _build_auth_state(self) -> JDGoldAuthState:
        """构造待持久化的授权状态快照。"""
        state = JDGoldAuthState(
            access_token=self._access_token,
            expires_at=self._token_deadline,
            expired_token_fingerprint=self._expired_token_fingerprint,
        )
        if self._login_session is not None:
            state.login_state, state.login_verifier, state.login_url, state.login_expires_at = self._login_session
        return state

    def _restore_auth(self) -> None:
        """同步加载跨进程授权状态，仅供初始化阶段调用。"""
        if self._auth_store is None:
            return
        self._apply_auth_state(self._auth_store.load())

    def _persist_auth(self) -> None:
        """同步保存凭据和待授权会话，仅供初始化阶段调用。"""
        if self._auth_store is None:
            return
        self._auth_store.save(self._build_auth_state())

    async def _restore_auth_async(self) -> None:
        """在线程池中加载 SQLite 授权状态，避免阻塞异步事件循环。"""
        if self._auth_store is None:
            return
        state = await asyncio.to_thread(self._auth_store.load)
        self._apply_auth_state(state)

    async def _persist_auth_async(self) -> None:
        """在线程池中保存 SQLite 授权状态，避免阻塞异步事件循环。"""
        if self._auth_store is None:
            return
        state = self._build_auth_state()
        await asyncio.to_thread(self._auth_store.save, state)

    @staticmethod
    def _validate_positive_number(name: str, value: str | int | float | None) -> None:
        """校验显式模拟交易的数值参数为有限正数。"""
        if value is None:
            return
        if isinstance(value, bool):
            raise ValueError(f"{name} 必须为正数")
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} 必须为正数") from None
        if not math.isfinite(numeric) or numeric <= 0:
            raise ValueError(f"{name} 必须为有限正数")

    @staticmethod
    def _is_allowed_authorize_host(host: str) -> bool:
        """判断授权主机名是否属于京东官方域名或 RFC 2606 测试保留域。"""
        lowered = host.lower().rstrip(".")
        return lowered in {"jd.com", "jdjygold.com"} or lowered.endswith((".jd.com", ".jdjygold.com", ".test"))

    def _validate_authorize_url(self, url: object) -> SecretStr:
        """校验授权地址必须为不含凭据信息的有效 HTTPS 链接且指向可信域名。"""
        if not isinstance(url, str):
            raise JDGoldError("INVALID_ENVELOPE")
        try:
            parsed = httpx.URL(url)
        except httpx.InvalidURL:
            raise JDGoldError("INVALID_ENVELOPE") from None
        if parsed.scheme != "https" or not parsed.host or parsed.username or parsed.password:
            raise JDGoldError("INVALID_ENVELOPE")
        if not self._is_allowed_authorize_host(parsed.host):
            raise JDGoldError("INVALID_ENVELOPE")
        return SecretStr(url)

    async def _token_async(self) -> str:
        """异步获取已配置的访问令牌，过期或缺失时抛出凭据异常。"""
        async with self._login_lock:
            await self._restore_auth_async()
            if self._token_deadline is not None and time.time() >= self._token_deadline + JD_GOLD_REAUTH_LEAD_SECONDS:
                self._expired_token_fingerprint = self._build_auth_state().credential_fingerprint
                self._access_token = SecretStr("")
                self._token_deadline = None
                await self._persist_auth_async()
            token = self._access_token.get_secret_value()
        if not token:
            raise JDGoldError("AUTH_REQUIRED", "京东黄金接口需要登录凭据")
        return token

    async def _ensure_token(self) -> str:
        """缺少有效凭据时自动准备授权，等待用户完成浏览器登录。"""
        try:
            return await self._token_async()
        except JDGoldError:
            pass
        raise JDGoldLoginRequiredError(await self.login())

    def _unwrap(self, raw: dict[str, JsonValue], kind: str) -> JsonValue:
        """解包并校验各上游接口业务信封与状态码。"""
        if kind == "public":
            if raw.get("success") is not True or type(raw.get("resultCode")) is not int or raw["resultCode"] != 0:
                raise JDGoldError(str(raw.get("resultCode", "INVALID_ENVELOPE")))
            inner = raw.get("resultData")
            if not isinstance(inner, dict) or "data" not in inner:
                raise JDGoldError("INVALID_ENVELOPE")
            if inner.get("success") is False or inner.get("status") in ("FAIL", "FAILED", "ERROR"):
                raise JDGoldError("UPSTREAM_ERROR")
            if "code" in inner and (
                type(inner["code"]) not in (int, str) or inner["code"] not in (0, 200, "0000", "000000")
            ):
                raise JDGoldError(str(inner["code"]))
            return inner["data"]
        if kind in {"gateway", "gold_analysis"}:
            if raw.get("success") is not True or "data" not in raw:
                raise JDGoldError(str(raw.get("code", "INVALID_ENVELOPE")))
            if (kind == "gold_analysis" or "code" in raw) and raw.get("code") != "0000":
                raise JDGoldError(str(raw.get("code", "INVALID_ENVELOPE")))
            return raw["data"]
        target = raw
        if "resultCode" in target or "resultData" in target:
            if (
                type(target.get("resultCode")) is not int
                or target["resultCode"] != 0
                or target.get("success") is False
                or "resultData" not in target
            ):
                raise JDGoldError(str(target.get("resultCode", "INVALID_ENVELOPE")))
            nested = target["resultData"]
            if not isinstance(nested, dict):
                raise JDGoldError("INVALID_ENVELOPE")
            target = nested
        if target.get("success") is False:
            if target.get("code") in (401, 403, "401", "403"):
                raise JDGoldError(str(target["code"]))
            raise JDGoldError("UPSTREAM_ERROR")
        if target.get("code") not in (0, 200) or type(target.get("code")) is not int or "data" not in target:
            raise JDGoldError(str(target.get("code", "INVALID_ENVELOPE")))
        return target["data"]

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, JsonValue] | None = None,
        kind: str = "bff",
        skill_code: str = "gold-analysis-hub",
        raw: bool = False,
    ) -> JsonValue:
        """底层 HTTP 请求执行与通用信封反序列化。"""
        headers = {"x-claw": self._claw} if self._claw else {}
        if kind in {"gateway", "gold_analysis"}:
            api_key = self._gateway_api_key.get_secret_value()
            if not api_key:
                raise JDGoldError("API_KEY_REQUIRED", "京东黄金分析接口需要访问密钥")
            headers.update(
                {
                    "apikey": api_key,
                    "x-skill-code": skill_code,
                    "x-skill-run-id": str(uuid4()),
                    "Accept": "application/json",
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                }
            )
        query_params = (
            {k: v if isinstance(v, (str, int, float, bool)) or v is None else str(v) for k, v in data.items()}
            if method == "GET" and data is not None
            else None
        )
        try:
            response = await self._http.request(
                method,
                url,
                params=query_params,
                json=data if method == "POST" else None,
                headers=headers,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise JDGoldError("NETWORK_ERROR") from None
        if not response.is_success:
            raise JDGoldError(str(response.status_code))
        try:
            payload = response.json()
        except ValueError:
            raise JDGoldError("INVALID_JSON") from None
        if not isinstance(payload, dict):
            raise JDGoldError("INVALID_ENVELOPE")
        if raw:
            if (
                payload.get("success") is not True
                or type(payload.get("resultCode")) is not int
                or payload["resultCode"] != 0
            ):
                raise JDGoldError(str(payload.get("resultCode", "INVALID_ENVELOPE")))
            return payload
        return self._unwrap(payload, kind)

    async def _bff(
        self,
        path: str,
        data: dict[str, JsonValue],
        *,
        method: str = "POST",
        auth: bool = False,
    ) -> JsonValue:
        """发起 BFF 业务网关接口请求。"""
        # 复制入参字典再注入令牌，避免原地修改调用方参数。
        payload: dict[str, JsonValue] = {
            key: value for key, value in data.items() if value is not None and (method != "GET" or value != "")
        }
        token: str | None = None
        if auth:
            token = await self._ensure_token()
            payload["accessToken"] = token
        try:
            return await self._request(method, self._bff_base_url + path, data=payload)
        except JDGoldError as error:
            if auth and error.code == "401":
                async with self._login_lock:
                    await self._restore_auth_async()
                    if token is not None and self._access_token.get_secret_value() == token:
                        self._expired_token_fingerprint = self._build_auth_state().credential_fingerprint
                        self._access_token = SecretStr("")
                        self._token_deadline = None
                        await self._persist_auth_async()
                raise JDGoldError("AUTH_REQUIRED", "京东登录授权已失效，请重新登录") from None
            raise

    async def _gold_analysis(
        self,
        path: str,
        data: dict[str, JsonValue],
        *,
        kind: str = "gold_analysis",
    ) -> JsonValue:
        """发起黄金分析智能网关接口请求。

        kind 默认为 gold_analysis（校验 code == "0000"）；
        realtime-quote 端点使用标准 gateway 信封，调用方需显式传入 kind="gateway"。
        """
        return await self._request(
            "POST",
            self._gateway_base_url + "/gold/" + path,
            data=data,
            kind=kind,
        )

    async def auth_login_url(self, challenge: str, state: str, local_redirect: str | None = None) -> JsonValue:
        """获取登录授权跳转地址。"""
        return await self._bff(
            "/getLoginUrl", {"challenge": challenge, "state": state, "localRedirect": local_redirect}
        )

    async def auth_status(self, state: str) -> JsonValue:
        """查询授权登录状态。"""
        return await self._bff("/status", {"state": state})

    async def auth_exchange(self, state: str, code: str) -> JsonValue:
        """换取业务授权凭据。"""
        return await self._bff("/exchange", {"state": state, "code": code})

    async def login(self) -> SecretStr:
        """生成 PKCE 授权链接；十分钟内复用同一待完成会话。"""
        async with self._login_lock:
            await self._restore_auth_async()
            if self._login_session is not None and time.time() < self._login_session[3]:
                return self._login_session[2]
            self._login_session = None
            state = secrets.token_urlsafe(24)
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
            result = await self.auth_login_url(challenge, state)
            if not isinstance(result, dict):
                raise JDGoldError("INVALID_ENVELOPE")
            authorize_url = self._validate_authorize_url(result.get("authorizeUrl"))
            await self._restore_auth_async()
            if self._login_session is not None and time.time() < self._login_session[3]:
                return self._login_session[2]
            self._login_session = (state, SecretStr(verifier), authorize_url, time.time() + 600)
            await self._persist_auth_async()
            return authorize_url

    async def complete_login(self) -> None:
        """仅在用户确认浏览器授权完成后调用；按原协议将毫秒有效期换算为秒。"""
        async with self._login_lock:
            await self._restore_auth_async()
            session = self._login_session
            if session is None or time.time() >= session[3]:
                self._login_session = None
                await self._persist_auth_async()
                raise JDGoldError("AUTH_REQUIRED", "京东授权会话不存在或已过期，请重新登录")
            result = await self.auth_exchange(session[0], session[1].get_secret_value())
            if not isinstance(result, dict):
                raise JDGoldError("INVALID_ENVELOPE")
            token, expires_in = result.get("accessToken"), result.get("expiresIn")
            if (
                not isinstance(token, str)
                or not token
                or not isinstance(expires_in, (int, float))
                or isinstance(expires_in, bool)
                or not math.isfinite(expires_in)
                or expires_in <= 0
            ):
                raise JDGoldError("INVALID_ENVELOPE")
            self._access_token = SecretStr(token)
            self._expired_token_fingerprint = None
            self._token_deadline = time.time() + expires_in / 1000 - JD_GOLD_REAUTH_LEAD_SECONDS
            self._login_session = None
            await self._persist_auth_async()

    async def logout(self) -> None:
        """清除本地保存的京东访问令牌与待确认登录会话。"""
        async with self._login_lock:
            self._access_token = SecretStr("")
            self._token_deadline = None
            self._expired_token_fingerprint = None
            self._login_session = None
            await self._persist_auth_async()

    async def query_price(self, unique_code: str) -> JsonValue:
        """查询指定黄金资产公开参考行情。"""
        return await self._bff("/queryPrice", {"uniqueCode": unique_code}, method="GET")

    async def holdings(self) -> JsonValue:
        """查询账户黄金持仓明细。"""
        return await self._bff("/queryHoldings", {}, method="GET", auth=True)

    async def morning_report(self, page_size: PositiveInt = 20) -> JsonValue:
        """查询黄金早报列表。"""
        return await self._bff("/queryMorningReport", {"pageSize": page_size}, method="GET", auth=True)

    async def news_flash(
        self,
        tag_id: PositiveInt = 20225,
        page_size: PositiveInt = 10,
        last_id: str = "",
    ) -> JsonValue:
        """查询黄金快讯列表。"""
        return await self._bff(
            "/queryNewsFlash", {"tagId": tag_id, "pageSize": page_size, "lastId": last_id}, method="GET"
        )

    async def order_list(
        self,
        order_create_start_date: str,
        order_create_end_date: str,
        **params: JsonValue,
    ) -> JsonValue:
        """查询历史订单交易记录。"""
        body: dict[str, JsonValue] = {
            "orderCreateStartDate": order_create_start_date,
            "orderCreateEndDate": order_create_end_date,
            "pageNo": 1,
            "pageSize": 50,
            "businessCode": "",
            "statusList": [],
            "bizChannelIds": [],
            "bizProductIdList": [],
            "tradeTypeCodeList": [],
            **params,
        }
        return await self._bff("/queryOrderList", body, auth=True)

    async def order_sum(
        self,
        order_create_start_date: str,
        order_create_end_date: str,
        **params: JsonValue,
    ) -> JsonValue:
        """查询指定时间段内的订单汇总统计。"""
        return await self._bff(
            "/queryOrderSum",
            {"orderCreateStartDate": order_create_start_date, "orderCreateEndDate": order_create_end_date, **params},
            auth=True,
        )

    async def conditional_orders(
        self,
        bank_code: str,
        start_time: NonNegativeInt,
        end_time: NonNegativeInt,
        *,
        jrid: str = "",
        **params: JsonValue,
    ) -> JsonValue:
        """查询条件挂单列表。"""
        return await self._bff(
            "/queryList",
            {
                "bankCode": bank_code,
                "startTime": start_time,
                "endTime": end_time,
                "jrid": jrid,
                "statusList": ["1"],
                "pageIndex": 1,
                "pageSize": 10,
                **params,
            },
            auth=True,
        )

    async def conditional_order_detail(
        self,
        bank_code: str,
        conditional_uuid: str,
        *,
        jrid: str = "",
    ) -> JsonValue:
        """查询指定条件挂单详情。"""
        return await self._bff(
            "/queryDetail", {"bankCode": bank_code, "conditionalUuid": conditional_uuid, "jrid": jrid}, auth=True
        )

    async def income_calendar(
        self,
        time_type: str,
        time: str,
        trade_type: str = "1",
        **params: JsonValue,
    ) -> JsonValue:
        """查询收益日历数据。"""
        return await self._bff(
            "/queryIncomeCalendar",
            {"calendarParam": {"timeType": time_type, "time": time, "tradeType": trade_type, **params}},
            auth=True,
        )

    async def sim_account(self, account_type: PositiveInt = 1) -> JsonValue:
        """查询并初始化显式模拟账户。"""
        return await self._bff("/queryAndInitAccount", {"accountType": account_type}, auth=True)

    async def sim_time_sharing(
        self,
        unique_code: str,
        type_: str = "m1",
        **params: JsonValue,
    ) -> JsonValue:
        """查询模拟分时走势。"""
        return await self._bff("/getTimeSharingInfos", {"uniqueCode": unique_code, "type": type_, **params}, auth=True)

    async def sim_kline(
        self,
        unique_code: str,
        k_type: str = "day",
        af_type: str = "bfq",
        **params: JsonValue,
    ) -> JsonValue:
        """查询模拟 K 线图表。"""
        return await self._bff(
            "/getKlineCandleDots", {"uniqueCode": unique_code, "kType": k_type, "afType": af_type, **params}, auth=True
        )

    async def sim_buy(
        self,
        trade_unit: PositiveInt,
        bus_id: str,
        *,
        trade_amount: str | PositiveInt | PositiveFloat | None = None,
        trade_gram: str | PositiveInt | PositiveFloat | None = None,
        account_type: PositiveInt = 1,
    ) -> JsonValue:
        """执行模拟买入交易。"""
        self._validate_positive_number("trade_unit", trade_unit)
        self._validate_positive_number("account_type", account_type)
        self._validate_positive_number("trade_amount", trade_amount)
        self._validate_positive_number("trade_gram", trade_gram)
        return await self._bff(
            "/buyGold",
            {
                "tradeUnit": trade_unit,
                "busId": bus_id,
                "accountType": account_type,
                "tradeAmount": str(trade_amount) if trade_amount is not None else None,
                "tradeGram": str(trade_gram) if trade_gram is not None else None,
            },
            auth=True,
        )

    async def sim_sell(
        self,
        trade_unit: PositiveInt,
        bus_id: str,
        *,
        trade_gram: str | PositiveInt | PositiveFloat | None = None,
        trade_ratio: str | PositiveInt | PositiveFloat | None = None,
        account_type: PositiveInt = 1,
    ) -> JsonValue:
        """执行模拟卖出交易。"""
        self._validate_positive_number("trade_unit", trade_unit)
        self._validate_positive_number("account_type", account_type)
        self._validate_positive_number("trade_gram", trade_gram)
        self._validate_positive_number("trade_ratio", trade_ratio)
        return await self._bff(
            "/sellGold",
            {
                "tradeUnit": trade_unit,
                "busId": bus_id,
                "accountType": account_type,
                "tradeGram": str(trade_gram) if trade_gram is not None else None,
                "tradeRatio": str(trade_ratio) if trade_ratio is not None else None,
            },
            auth=True,
        )

    async def sim_trade_overview(self, account_type: PositiveInt = 1) -> JsonValue:
        """查询模拟交易概览。"""
        return await self._bff("/queryTradeOverview", {"accountType": account_type}, auth=True)

    async def sim_trade_records(
        self,
        last_id: str | None = None,
        page_size: PositiveInt = 8,
        account_type: PositiveInt = 1,
    ) -> JsonValue:
        """分页查询模拟交易记录流水。"""
        return await self._bff(
            "/queryTradeRecords", {"lastId": last_id, "pageSize": page_size, "accountType": account_type}, auth=True
        )

    async def stock_quote(self, stock_code: str) -> JsonValue:
        """查询上海黄金交易所贵金属现货行情。"""
        return await self._request(
            "POST",
            self._public_base_url + "/queryStockData",
            data={"paramMap": {"stockCode": stock_code}},
            kind="public",
        )

    async def stock_kline(self, stock_code: str, k_type: str = "day") -> JsonValue:
        """查询贵金属 K 线数据。"""
        return await self._request(
            "POST",
            self._public_base_url + "/queryStockKLine",
            data={"paramMap": {"stockCode": stock_code, "kType": k_type}},
            kind="public",
        )

    async def news(self, query: str = "黄金", size: PositiveInt = 5) -> JsonValue:
        """检索黄金资讯列表。"""
        return await self._request(
            "POST",
            self._public_base_url + "/queryInformation",
            data={"query": query, "size": size},
            kind="public",
        )

    async def gold_realtime_quote(self, unique_code: str) -> JsonValue:
        """查询分析网关实时行情（使用标准 gateway 信封）。"""
        return await self._gold_analysis("realtime-quote", {"uniqueCode": unique_code}, kind="gateway")

    async def gold_bomb(self) -> JsonValue:
        """查询突发大单异动指标。"""
        return await self._gold_analysis("bomb", {})

    async def gold_bomb_details(self) -> JsonValue:
        """查询突发大单异动明细。"""
        return await self._gold_analysis("bomb-details", {})

    async def gold_opportunity(self) -> JsonValue:
        """查询黄金套利或波段机会分析。"""
        return await self._gold_analysis("opportunity", {})

    async def gold_order_book(self) -> JsonValue:
        """查询黄金盘口挂单分布。"""
        return await self._gold_analysis("order-book", {})

    async def gold_indicator_resonance(self) -> JsonValue:
        """查询多指标共振信号分析。"""
        return await self._gold_analysis("indicator-resonance", {})

    async def gold_ranking(self, rank_type: str = "1") -> JsonValue:
        """查询黄金达人持仓或策略排行。"""
        return await self._request(
            "POST",
            self._gateway_base_url + "/rank/gold-regular",
            data={"rankType": rank_type},
            kind="gateway",
            skill_code="blogger-trend",
        )

    async def fund_ranking(self, rank_type: PositiveInt = 401, **params: JsonValue) -> JsonValue:
        """查询黄金基金多维排行。"""
        return await self._request(
            "POST",
            self._gateway_base_url + "/rank/fund-multi",
            data={
                "rankType": rank_type,
                "rankSortBy": 0,
                "pageSize": 10,
                "isNeedShowColumnName": True,
                "isNeedRecommendSku": True,
                "isNeedBestRank": False,
                "isNeedSingleSkuRankRadio": False,
                "isNeedFlowStatus": False,
                **params,
            },
            kind="gateway",
            skill_code="blogger-trend",
        )

    async def latest_price(self) -> dict[str, JsonValue]:
        """获取最新参考报价原始信封。"""
        result = await self._request("POST", self._latest_price_url, raw=True)
        if not isinstance(result, dict):
            raise JDGoldError("INVALID_ENVELOPE")
        return result
