"""京东业务协议、信封校验和凭据边界测试。"""

import asyncio
import base64
import hashlib
import json
import traceback

import httpx
import pytest

from src.clients.jd_gold import JDGoldClient, JDGoldError, JDGoldLoginRequiredError


def test_pkce_login_then_query_and_expiry(monkeypatch) -> None:
    async def run() -> None:
        now = 1000.0
        monkeypatch.setattr("src.clients.jd_gold.time.time", lambda: now)
        requests = []
        login_body = {}

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal login_body
            requests.append(request)
            if request.url.path.endswith("/getLoginUrl"):
                login_body = json.loads(request.content)
                assert len(login_body["state"]) >= 32
                assert "localRedirect" not in login_body
                data = {"authorizeUrl": "https://login.test/authorize?secret=hidden"}
            elif request.url.path.endswith("/exchange"):
                body = json.loads(request.content)
                assert body["state"] == login_body["state"]
                assert 43 <= len(body["code"]) <= 128
                digest = hashlib.sha256(body["code"].encode("ascii")).digest()
                assert base64.urlsafe_b64encode(digest).decode().rstrip("=") == login_body["challenge"]
                assert not request.url.query
                data = {"accessToken": "private-token", "expiresIn": 28_800_000}
            else:
                assert request.url.params["accessToken"] == "private-token"
                data = {"holdings": []}
            return httpx.Response(200, json={"code": 200, "data": data})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = JDGoldClient(http)
            results = await asyncio.gather(client.holdings(), client.morning_report(), return_exceptions=True)
            assert all(isinstance(result, JDGoldLoginRequiredError) for result in results)
            assert len(requests) == 1
            assert "hidden" not in repr(results)
            await client.complete_login()
            assert await client.holdings() == {"holdings": []}
            assert len(requests) == 3
            now += 28_800
            with pytest.raises(JDGoldLoginRequiredError):
                await client.holdings()
            assert len(requests) == 4
            assert login_body["state"] != json.loads(requests[0].content)["state"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "data", [None, {}, {"accessToken": "secret", "expiresIn": True}, {"accessToken": "secret", "expiresIn": 0}]
)
def test_login_rejects_invalid_credentials(data) -> None:
    async def run() -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            result = (
                {"authorizeUrl": "https://login.test/authorize"} if request.url.path.endswith("/getLoginUrl") else data
            )
            return httpx.Response(200, json={"code": 200, "data": result})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = JDGoldClient(http)
            await client.login()
            with pytest.raises(JDGoldError, match="INVALID_ENVELOPE"):
                await client.complete_login()

    asyncio.run(run())


def test_login_session_expires_without_exchange(monkeypatch) -> None:
    async def run() -> None:
        now = 1000.0
        monkeypatch.setattr("src.clients.jd_gold.time.time", lambda: now)

        def handle(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/getLoginUrl")
            return httpx.Response(200, json={"code": 200, "data": {"authorizeUrl": "https://login.test/authorize"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = JDGoldClient(http)
            with pytest.raises(JDGoldError, match="请重新登录"):
                await client.complete_login()
            await client.login()
            now += 600
            with pytest.raises(JDGoldError, match="请重新登录"):
                await client.complete_login()

    asyncio.run(run())


@pytest.mark.parametrize("code", [401, 403])
def test_auth_failure_does_not_replay_trade(code) -> None:
    async def run() -> None:
        calls = []

        def handle(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path.endswith("/getLoginUrl"):
                return httpx.Response(200, json={"code": 200, "data": {"authorizeUrl": "https://login.test/authorize"}})
            return httpx.Response(code)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = JDGoldClient(http, access_token="old-token")
            with pytest.raises(JDGoldError) as error:
                await client.sim_buy(1, "bus", trade_amount=100)
            assert error.value.code == ("AUTH_REQUIRED" if code == 401 else "403")
            assert len(calls) == 1
            if code == 401:
                with pytest.raises(JDGoldLoginRequiredError):
                    await client.holdings()
                assert calls[-1].url.path.endswith("/getLoginUrl")

    asyncio.run(run())


CASES = [
    (
        "auth_login_url",
        ("challenge", "state"),
        {},
        "POST",
        "bff/getLoginUrl",
        {"challenge": "challenge", "state": "state"},
    ),
    ("auth_status", ("state",), {}, "POST", "bff/status", {"state": "state"}),
    ("auth_exchange", ("state", "verifier"), {}, "POST", "bff/exchange", {"state": "state", "code": "verifier"}),
    ("query_price", ("CMBC-JCJ",), {}, "GET", "bff/queryPrice", {"uniqueCode": "CMBC-JCJ"}),
    ("holdings", (), {}, "GET", "bff/queryHoldings", {"accessToken": "token"}),
    ("morning_report", (), {}, "GET", "bff/queryMorningReport", {"accessToken": "token", "pageSize": "20"}),
    ("news_flash", (), {}, "GET", "bff/queryNewsFlash", {"pageSize": "10", "tagId": "20225"}),
    (
        "order_list",
        ("20261101", "20261118"),
        {},
        "POST",
        "bff/queryOrderList",
        {
            "accessToken": "token",
            "orderCreateStartDate": "20261101",
            "orderCreateEndDate": "20261118",
            "pageNo": 1,
            "pageSize": 50,
            "businessCode": "",
            "statusList": [],
            "bizChannelIds": [],
            "bizProductIdList": [],
            "tradeTypeCodeList": [],
        },
    ),
    (
        "order_sum",
        ("20261101", "20261118"),
        {},
        "POST",
        "bff/queryOrderSum",
        {"accessToken": "token", "orderCreateStartDate": "20261101", "orderCreateEndDate": "20261118"},
    ),
    (
        "conditional_orders",
        ("CMBC", 100, 200),
        {},
        "POST",
        "bff/queryList",
        {
            "accessToken": "token",
            "bankCode": "CMBC",
            "startTime": 100,
            "endTime": 200,
            "jrid": "",
            "statusList": ["1"],
            "pageIndex": 1,
            "pageSize": 10,
        },
    ),
    (
        "conditional_order_detail",
        ("CMBC", "uuid"),
        {},
        "POST",
        "bff/queryDetail",
        {"accessToken": "token", "bankCode": "CMBC", "conditionalUuid": "uuid", "jrid": ""},
    ),
    (
        "income_calendar",
        ("day", "202611"),
        {},
        "POST",
        "bff/queryIncomeCalendar",
        {"accessToken": "token", "calendarParam": {"timeType": "day", "time": "202611", "tradeType": "1"}},
    ),
    ("sim_account", (), {}, "POST", "bff/queryAndInitAccount", {"accessToken": "token", "accountType": 1}),
    (
        "sim_time_sharing",
        ("WG-JDAU",),
        {"from": "100", "nums": 20},
        "POST",
        "bff/getTimeSharingInfos",
        {"accessToken": "token", "uniqueCode": "WG-JDAU", "type": "m1", "from": "100", "nums": 20},
    ),
    (
        "sim_kline",
        ("WG-JDAU",),
        {},
        "POST",
        "bff/getKlineCandleDots",
        {"accessToken": "token", "uniqueCode": "WG-JDAU", "kType": "day", "afType": "bfq"},
    ),
    (
        "sim_buy",
        (1, "bus"),
        {"trade_amount": "100"},
        "POST",
        "bff/buyGold",
        {"accessToken": "token", "accountType": 1, "tradeUnit": 1, "busId": "bus", "tradeAmount": "100"},
    ),
    (
        "sim_sell",
        (3, "bus"),
        {"trade_ratio": "0.5"},
        "POST",
        "bff/sellGold",
        {"accessToken": "token", "accountType": 1, "tradeUnit": 3, "busId": "bus", "tradeRatio": "0.5"},
    ),
    ("sim_trade_overview", (), {}, "POST", "bff/queryTradeOverview", {"accessToken": "token", "accountType": 1}),
    (
        "sim_trade_records",
        (),
        {"last_id": "cursor"},
        "POST",
        "bff/queryTradeRecords",
        {"accessToken": "token", "accountType": 1, "pageSize": 8, "lastId": "cursor"},
    ),
    ("stock_quote", ("SGE-Au99.99",), {}, "POST", "public/queryStockData", {"paramMap": {"stockCode": "SGE-Au99.99"}}),
    (
        "stock_kline",
        ("SGE-Au99.99", "week"),
        {},
        "POST",
        "public/queryStockKLine",
        {"paramMap": {"stockCode": "SGE-Au99.99", "kType": "week"}},
    ),
    ("news", (), {}, "POST", "public/queryInformation", {"query": "黄金", "size": 5}),
    ("gold_realtime_quote", ("CMBC-JCJ",), {}, "POST", "gateway/gold/realtime-quote", {"uniqueCode": "CMBC-JCJ"}),
    ("gold_bomb", (), {}, "POST", "gateway/gold/bomb", {}),
    ("gold_bomb_details", (), {}, "POST", "gateway/gold/bomb-details", {}),
    ("gold_opportunity", (), {}, "POST", "gateway/gold/opportunity", {}),
    ("gold_order_book", (), {}, "POST", "gateway/gold/order-book", {}),
    ("gold_indicator_resonance", (), {}, "POST", "gateway/gold/indicator-resonance", {}),
    ("gold_ranking", (), {}, "POST", "gateway/rank/gold-regular", {"rankType": "1"}),
    (
        "fund_ranking",
        (),
        {},
        "POST",
        "gateway/rank/fund-multi",
        {
            "rankType": 401,
            "rankSortBy": 0,
            "pageSize": 10,
            "isNeedShowColumnName": True,
            "isNeedRecommendSku": True,
            "isNeedBestRank": False,
            "isNeedSingleSkuRankRadio": False,
            "isNeedFlowStatus": False,
        },
    ),
    ("latest_price", (), {}, "POST", "latest", None),
]


@pytest.mark.parametrize(("name", "args", "kwargs", "method", "path", "body"), CASES, ids=[case[0] for case in CASES])
def test_all_endpoint_contracts(name, args, kwargs, method, path, body) -> None:
    async def run() -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            assert request.method == method
            assert request.url.path == "/" + path
            assert request.headers["x-claw"] == "codex"
            if method == "GET":
                assert dict(request.url.params) == body
            else:
                assert (json.loads(request.content) if request.content else None) == body
            if path.startswith("gateway/"):
                assert request.headers["apikey"] == "key"
                assert request.headers["x-skill-code"] == ("blogger-trend" if "/rank/" in path else "gold-analysis-hub")
                assert request.headers["x-skill-run-id"]
                return httpx.Response(200, json={"success": True, "code": "0000", "data": {"ok": 1}})
            assert "apikey" not in request.headers
            if path.startswith("public/"):
                return httpx.Response(200, json={"success": True, "resultCode": 0, "resultData": {"data": {"ok": 1}}})
            if name == "latest_price":
                return httpx.Response(200, json={"success": True, "resultCode": 0, "resultData": {"datas": {"ok": 1}}})
            return httpx.Response(
                200, json={"resultCode": 0, "success": True, "resultData": {"code": 200, "data": {"ok": 1}}}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = JDGoldClient(
                http,
                bff_base_url="https://example.test/bff",
                public_base_url="https://example.test/public",
                gateway_base_url="https://example.test/gateway",
                latest_price_url="https://example.test/latest",
                access_token="token",
                gateway_api_key="key",
                claw="codex",
            )
            result = await getattr(client, name)(*args, **kwargs)
            assert result == (
                {"success": True, "resultCode": 0, "resultData": {"datas": {"ok": 1}}}
                if name == "latest_price"
                else {"ok": 1}
            )
            assert not http.is_closed

    asyncio.run(run())


@pytest.mark.parametrize(
    ("name", "args", "kwargs", "method", "path", "body"),
    [
        case
        for case in CASES
        if case[0]
        in {
            "holdings",
            "morning_report",
            "order_list",
            "order_sum",
            "conditional_orders",
            "conditional_order_detail",
            "income_calendar",
        }
        or case[0].startswith("sim_")
    ],
    ids=lambda value: str(value),
)
def test_account_calls_require_token(name, args, kwargs, method, path, body) -> None:
    async def run() -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/getLoginUrl")
            return httpx.Response(200, json={"code": 200, "data": {"authorizeUrl": "https://login.test/authorize"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            with pytest.raises(JDGoldLoginRequiredError) as error:
                await getattr(JDGoldClient(http), name)(*args, **kwargs)
            assert error.value.code == "AUTH_REQUIRED"
            assert error.value.authorize_url.get_secret_value() == "https://login.test/authorize"
            assert "login.test" not in str(error.value)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("name", "args", "response", "code"),
    [
        ("query_price", ("x",), {}, "INVALID_ENVELOPE"),
        ("query_price", ("x",), {"code": 500, "msg": "secret"}, "500"),
        ("query_price", ("x",), {"resultCode": 0, "resultData": {"code": 400, "msg": "secret"}}, "400"),
        ("stock_quote", ("x",), {"success": False, "resultCode": 3}, "3"),
        ("stock_quote", ("x",), {"success": True, "resultCode": 0, "resultData": {}}, "INVALID_ENVELOPE"),
        (
            "stock_quote",
            ("x",),
            {"success": True, "resultCode": 0, "resultData": {"code": 503, "data": {"secret": "secret"}}},
            "503",
        ),
        (
            "stock_quote",
            ("x",),
            {"success": True, "resultCode": 0, "resultData": {"success": False, "data": {}}},
            "UPSTREAM_ERROR",
        ),
        (
            "stock_quote",
            ("x",),
            {"success": True, "resultCode": 0, "resultData": {"status": "FAIL", "data": {}}},
            "UPSTREAM_ERROR",
        ),
        (
            "stock_quote",
            ("x",),
            {"success": True, "resultCode": 0, "resultData": {"code": False, "data": {}}},
            "UPSTREAM_ERROR",
        ),
        ("query_price", ("x",), {"code": 200, "success": False, "data": {}}, "UPSTREAM_ERROR"),
        ("query_price", ("x",), {"code": 200.0, "data": {}}, "UPSTREAM_ERROR"),
        ("latest_price", (), {"success": True, "resultCode": False}, "UPSTREAM_ERROR"),
        ("gold_bomb", (), {"success": True, "code": "9000", "data": []}, "9000"),
        ("gold_bomb", (), {"success": True, "data": []}, "INVALID_ENVELOPE"),
        ("gold_bomb", (), {"success": False, "code": "secret-token"}, "UPSTREAM_ERROR"),
        ("latest_price", (), {"success": False, "resultCode": 7}, "7"),
    ],
)
def test_bad_envelopes_are_rejected_without_secrets(name, args, response, code) -> None:
    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
        ) as http:
            with pytest.raises(JDGoldError) as error:
                await getattr(JDGoldClient(http, gateway_api_key="secret"), name)(*args)
            assert error.value.code == code
            assert "secret" not in str(error.value)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("failure", "code"),
    [("network", "NETWORK_ERROR"), (403, "403"), (500, "500"), ("json", "INVALID_JSON"), ("array", "INVALID_ENVELOPE")],
)
def test_transport_errors_are_sanitized_and_never_retried(failure, code) -> None:
    async def run() -> None:
        count = 0

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal count
            count += 1
            if failure == "network":
                raise httpx.ConnectError("secret token https://private.test", request=request)
            if failure == "json":
                return httpx.Response(200, text="secret token")
            if failure == "array":
                return httpx.Response(200, json=[])
            return httpx.Response(failure, text="secret token")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            with pytest.raises(JDGoldError) as error:
                await JDGoldClient(http, access_token="secret").sim_buy(1, "bus", trade_amount="1")
            assert error.value.code == code
            assert "secret" not in str(error.value)
            rendered = "".join(traceback.format_exception(error.value))
            assert "secret token" not in rendered
            assert "https://private.test" not in rendered
            assert code in str(error.value)
            assert error.value.__cause__ is None
            assert error.value.__suppress_context__ or error.value.__context__ is None
            assert count == 1

    asyncio.run(run())


def test_gateway_key_is_required_locally() -> None:
    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: pytest.fail("缺少密钥不得发送请求"))
        ) as http:
            with pytest.raises(JDGoldError, match="访问密钥"):
                await JDGoldClient(http).gold_bomb()

    asyncio.run(run())


@pytest.mark.parametrize("inner_code", [0, 200, "0000", "000000"])
def test_public_business_success_codes(inner_code) -> None:
    async def run() -> None:
        payload = {"success": True, "resultCode": 0, "resultData": {"code": inner_code, "data": {"price": "1"}}}
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as http:
            assert await JDGoldClient(http).stock_quote("SGE-Au99.99") == {"price": "1"}

    asyncio.run(run())


def test_sim_trade_does_not_follow_redirects() -> None:
    async def run() -> None:
        requests = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(307, headers={"Location": "https://elsewhere.test/duplicate-trade"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as http:
            with pytest.raises(JDGoldError) as error:
                await JDGoldClient(http, access_token="secret").sim_buy(1, "bus", trade_amount=100)
            assert error.value.code == "307"
            assert len(requests) == 1
            assert json.loads(requests[0].content)["tradeAmount"] == "100"

    asyncio.run(run())


@pytest.mark.parametrize(("method", "args"), [("stock_quote", ("SGE-Au99.99",)), ("news", ("黄金", 1))])
def test_public_real_gateway_success_envelope(method, args) -> None:
    async def run() -> None:
        payload = {
            "success": True,
            "resultCode": 0,
            "resultData": {"msg": "success", "code": "000000", "data": {"ok": True}},
        }
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as http:
            assert await getattr(JDGoldClient(http), method)(*args) == {"ok": True}

    asyncio.run(run())


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("sim_buy", {"trade_unit": 0, "bus_id": "bus", "trade_amount": 100}),
        ("sim_buy", {"trade_unit": 1, "bus_id": "bus", "trade_amount": -10}),
        ("sim_buy", {"trade_unit": 1, "bus_id": "bus", "trade_gram": "0"}),
        ("sim_sell", {"trade_unit": 1, "bus_id": "bus", "trade_gram": -1}),
        ("sim_sell", {"trade_unit": 1, "bus_id": "bus", "trade_ratio": "NaN"}),
    ],
)
def test_sim_trade_rejects_non_positive_numbers(method, kwargs) -> None:
    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: pytest.fail("非法参数不应发出网络请求"))
        ) as http:
            with pytest.raises(ValueError):
                await getattr(JDGoldClient(http, access_token="secret"), method)(**kwargs)

    asyncio.run(run())


def test_login_rejects_authorize_url_with_credentials() -> None:
    async def run() -> None:
        payload = {"code": 200, "data": {"authorizeUrl": "https://user:pass@login.test/authorize"}}
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as http:
            with pytest.raises(JDGoldError) as error:
                await JDGoldClient(http).login()
            assert error.value.code == "INVALID_ENVELOPE"

    asyncio.run(run())
