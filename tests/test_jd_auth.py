"""京东授权的 SQLite 恢复、失效清理与权限测试。"""

import asyncio
import json
import sqlite3
import stat
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from main import app
from src.clients.jd_browser_auth import JDGoldBrowserAuthorizer
from src.clients.jd_gold import JDGoldClient, JDGoldError, JDGoldLoginRequiredError
from src.configs.settings import Settings
from src.contracts.domains.jd_auth import JDGoldAuthState, JDGoldBrowserRenewError
from src.repositories.jd_gold_auth import JDGoldAuthRepository
from src.runtime.dependencies import RuntimeDependencies
from src.services.jd_gold.auth import JDGoldAuthService
from src.services.jd_gold.gateway import JDGoldGateway


def test_orm_reads_and_updates_legacy_database(tmp_path) -> None:
    path = tmp_path / "jd_auth.sqlite3"
    scope = "https://bff.test"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE jd_auth (scope TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        db.execute("INSERT INTO jd_auth VALUES (?, ?)", (scope, '{"access_token":"legacy-token","expires_at":2000}'))

    repo = JDGoldAuthRepository(path, scope=scope)
    assert repo.load().access_token.get_secret_value() == "legacy-token"
    repo.save(JDGoldAuthState(access_token=SecretStr("updated-token"), expires_at=3000))

    assert JDGoldAuthRepository(path, scope=scope).load().access_token.get_secret_value() == "updated-token"
    with closing(sqlite3.connect(path)) as db:
        (payload,) = db.execute("SELECT payload FROM jd_gold_auth WHERE scope = ?", (scope,)).fetchone()
    assert json.loads(payload)["access_token"] == "updated-token"


def test_sqlite_auth_scope_and_permissions(tmp_path) -> None:
    path = tmp_path / "private" / "jd_auth.sqlite3"
    state = JDGoldAuthState(
        access_token=SecretStr("private-token"),
        expires_at=2000,
        login_state="state",
        login_verifier=SecretStr("verifier"),
        login_url=SecretStr("https://login.test/authorize"),
        login_expires_at=1600,
    )
    repo = JDGoldAuthRepository(path, scope="https://bff.test/")
    repo.save(state)
    assert JDGoldAuthRepository(path, scope="https://bff.test").load() == state
    assert JDGoldAuthRepository(path, scope="https://other.test").load() == JDGoldAuthState()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "private-token" not in repr(state)
    assert "verifier" not in state.model_dump_json().replace('"login_verifier"', "")


@pytest.mark.parametrize("failure_code", [401, 403])
def test_login_survives_restart_and_invalid_credentials_are_cleared(tmp_path, monkeypatch, failure_code) -> None:
    async def run() -> None:
        now = 1000.0
        monkeypatch.setattr("src.clients.jd_gold.time.time", lambda: now)
        path = tmp_path / "jd_auth.sqlite3"
        calls = []

        def store() -> JDGoldAuthRepository:
            return JDGoldAuthRepository(path, scope="https://bff.test")

        def handle(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path.endswith("/getLoginUrl"):
                data = {"authorizeUrl": "https://login.test/authorize"}
            elif request.url.path.endswith("/exchange"):
                body = json.loads(request.content)
                pending = store().load()
                assert body == {"state": pending.login_state, "code": pending.login_verifier.get_secret_value()}
                data = {"accessToken": "private-token", "expiresIn": 28_800_000}
            else:
                assert request.url.params["accessToken"] == "private-token"
                return httpx.Response(failure_code)
            return httpx.Response(200, json={"code": 200, "data": data})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            first = JDGoldClient(http, auth_store=store())
            with pytest.raises(JDGoldLoginRequiredError):
                await first.holdings()
            restarted = JDGoldClient(http, auth_store=store())
            assert await restarted.login() == await first.login()
            assert len(calls) == 1
            await restarted.complete_login()
            assert store().load().access_token.get_secret_value() == "private-token"
            assert not store().load().login_state
            with pytest.raises(JDGoldError) as error:
                await first.holdings()
            assert error.value.code == ("AUTH_REQUIRED" if failure_code == 401 else "403")
            assert bool(store().load().access_token.get_secret_value()) == (failure_code != 401)

    asyncio.run(run())


def test_persisted_token_expires_at_upstream_deadline(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        now = 1000.0
        monkeypatch.setattr("src.clients.jd_gold.time.time", lambda: now)
        store = JDGoldAuthRepository(tmp_path / "jd_auth.sqlite3", scope="https://bff.test")
        store.save(JDGoldAuthState(access_token=SecretStr("expired-token"), expires_at=700))

        def handle(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/getLoginUrl")
            assert "expired-token" not in str(request.url)
            return httpx.Response(200, json={"code": 200, "data": {"authorizeUrl": "https://login.test/authorize"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            with pytest.raises(JDGoldLoginRequiredError):
                await JDGoldClient(http, auth_store=store).holdings()
            saved = store.load()
            assert not saved.access_token.get_secret_value()
            assert saved.expires_at is None
            assert saved.login_expires_at == now + 600

    asyncio.run(run())


def test_service_prepares_authorization_before_expiry_and_keeps_token_usable(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        now = 999.0
        monkeypatch.setattr("src.services.jd_gold.gateway.time.time", lambda: now)
        settings = Settings(
            data_dir=str(tmp_path),
            jd_access_token=SecretStr(""),
            jd_proactive_auth_enabled=True,
            jd_auto_renew_enabled=False,
            jd_auto_renew_lead_seconds=300,
        )
        repo = JDGoldAuthRepository(settings.database_path, scope=settings.jd_bff_base_url)
        repo.save(JDGoldAuthState(access_token=SecretStr("current-token"), expires_at=1000))
        authorization_requested = asyncio.Event()
        calls = []

        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path.rsplit("/", 1)[-1]
            calls.append(path)
            if path == "getLoginUrl":
                authorization_requested.set()
                data = {"authorizeUrl": "https://login.test/authorize"}
            elif path == "queryHoldings":
                assert request.url.params["accessToken"] == "current-token"
                data = []
            else:
                pytest.fail(f"意外请求：{path}")
            return httpx.Response(200, json={"code": 200, "data": data})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            service = JDGoldGateway(http, settings)
            assert not await service.prepare_reauthorization()
            now = 1000.0
            await service.start()
            try:
                await asyncio.wait_for(authorization_requested.wait(), timeout=1)
                async with asyncio.timeout(1):
                    while not service.auth_state().login_state:
                        await asyncio.sleep(0.005)
                assert service.auth_state().login_state
                assert not await service.prepare_reauthorization()
                assert await service.client.holdings() == []
                now = 1300.0
                with pytest.raises(JDGoldLoginRequiredError):
                    await service.client.holdings()
                assert calls == ["getLoginUrl", "queryHoldings"]
            finally:
                await service.stop()

    asyncio.run(run())


def test_auth_state_actual_expires_at_and_settings_resolved_data_dir(tmp_path, monkeypatch) -> None:
    state = JDGoldAuthState(access_token=SecretStr("tok"), expires_at=1000.0)
    assert state.actual_expires_at == 1300.0
    assert JDGoldAuthState().actual_expires_at is None

    monkeypatch.chdir(tmp_path)
    relative_settings = Settings(data_dir="data/quotes")
    assert relative_settings.resolved_data_dir.is_absolute()
    assert relative_settings.resolved_data_dir.name == "quotes"
    assert relative_settings.database_path == relative_settings.resolved_data_dir / "quant-agentic.sqlite3"
    assert relative_settings.resolved_data_dir != tmp_path / "data" / "quotes"

    absolute_settings = Settings(data_dir=str(tmp_path / "custom"))
    assert absolute_settings.resolved_data_dir == tmp_path / "custom"


def test_jd_auth_api_full_lifecycle_login_complete_verify_logout(tmp_path) -> None:
    """验证账号管理接口完整流程：状态查询 -> 发起登录 -> 完成授权 -> 权限验证 -> 退出登录。"""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "getLoginUrl":
            return httpx.Response(200, json={"code": 200, "data": {"authorizeUrl": "https://login.test/authorize"}})
        if path == "exchange":
            return httpx.Response(200, json={"code": 200, "data": {"accessToken": "web-token", "expiresIn": 3_600_000}})
        if path == "queryHoldings":
            assert request.url.params["accessToken"] == "web-token"
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "totalGramAll": 10.5,
                        "avgCostPrice": 612.34,
                        "holdingList": [
                            {
                                "bankCode": "CMBC",
                                "bankName": "民生银行",
                                "totalGram": 10.5,
                                "avgCostPrice": 612.34,
                                "totalIncome": 120.5,
                            }
                        ],
                    },
                },
            )
        pytest.fail(f"意外上游请求：{path}")

    settings = Settings.model_construct(
        data_dir=str(tmp_path),
        timesfm_enabled=False,
        news_enabled=False,
        live_quotes_enabled=False,
        market_bars_enabled=False,
        news_sync_enabled=False,
        jd_holdings_sync_enabled=False,
        jd_proactive_auth_enabled=False,
        jd_auto_renew_enabled=False,
        jd_access_token=SecretStr(""),
    )
    runtime = RuntimeDependencies(settings)
    original_http = runtime.jd_http
    asyncio.run(original_http.aclose())
    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    runtime.jd_http = mock_http
    runtime.jd_gold = JDGoldGateway(mock_http, settings)
    runtime.jd_holdings._gateway = runtime.jd_gold
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime

    try:
        client = TestClient(app)

        status_resp = client.get("/api/v1/jd/auth/status")
        assert status_resp.status_code == 200
        assert status_resp.headers.get("cache-control") == "no-store"
        status_body = status_resp.json()
        assert status_body["logged_in"] is False
        assert status_body["pending_login"] is False
        assert status_body["authorize_url"] is None

        # 未发起登录前直接完成授权应返回 401
        early_complete = client.post("/api/v1/jd/auth/complete")
        assert early_complete.status_code == 401

        login_resp = client.post("/api/v1/jd/auth/login")
        assert login_resp.status_code == 200
        login_body = login_resp.json()
        assert login_body["authorize_url"] == "https://login.test/authorize"
        assert login_body["expires_at"] is not None

        pending_status = client.get("/api/v1/jd/auth/status").json()
        assert pending_status["pending_login"] is True
        assert pending_status["authorize_url"] == "https://login.test/authorize"

        complete_resp = client.post("/api/v1/jd/auth/complete")
        assert complete_resp.status_code == 200
        complete_body = complete_resp.json()
        assert complete_body["ok"] is True
        assert complete_body["status"]["logged_in"] is True
        assert complete_body["status"]["pending_login"] is False
        assert complete_body["status"]["usable_seconds"] > 0

        # 完成授权后已自动尝试同步一次持仓快照
        holdings_resp = client.get("/api/v1/jd/holdings/snapshot")
        assert holdings_resp.status_code == 200
        assert holdings_resp.json()["data"]["totalGramAll"] == 10.5

        verify_resp = client.post("/api/v1/jd/auth/verify")
        assert verify_resp.status_code == 200
        assert verify_resp.json()["ok"] is True

        logout_resp = client.post("/api/v1/jd/auth/logout")
        assert logout_resp.status_code == 200
        logout_body = logout_resp.json()
        assert logout_body["ok"] is True
        assert logout_body["status"]["logged_in"] is False
        assert logout_body["status"]["pending_login"] is False
    finally:
        app.dependency_overrides.clear()
        asyncio.run(runtime.close())


def test_auto_renew_triggers_30_minutes_before_expiry_and_updates_status(tmp_path, monkeypatch) -> None:
    """验证令牌到期前 30 分钟自动触发浏览器续约、换取新令牌并刷新状态摘要。"""

    async def run() -> None:
        now = 3199.0
        monkeypatch.setattr("src.clients.jd_gold.time.time", lambda: now)
        store = JDGoldAuthRepository(tmp_path / "jd_auth.sqlite3", scope="https://bff.test")
        # 存量格式：expires_at = 4700 -> actual_expires_at = 5000，到期前 30 分钟为 3200
        store.save(JDGoldAuthState(access_token=SecretStr("old-token"), expires_at=4700.0))

        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path.rsplit("/", 1)[-1]
            if path == "getLoginUrl":
                return httpx.Response(200, json={"code": 200, "data": {"authorizeUrl": "https://login.test/authorize"}})
            if path == "exchange":
                return httpx.Response(
                    200,
                    json={"code": 200, "data": {"accessToken": "renewed-token", "expiresIn": 28_800_000}},
                )
            pytest.fail(f"意外上游请求：{path}")

        seen_urls: list[str] = []

        class _FakeAuthorizer:
            async def authorize(self, authorize_url: str, complete_callback, *, headless=None) -> None:
                seen_urls.append(authorize_url)
                await complete_callback()

            async def interactive_login(self, authorize_url: str, complete_callback, *, timeout_seconds=300.0) -> None:
                await complete_callback()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = JDGoldClient(http, auth_store=store)
            auth_service = JDGoldAuthService(
                client,
                store,
                enabled=False,
                browser_authorizer=_FakeAuthorizer(),
                auto_renew_enabled=True,
                reauth_lead_seconds=1800,
            )
            # 距离到期 30 分 01 秒，不应触发续约
            assert await auth_service.prepare_reauthorization(now=3199.0) is False
            assert seen_urls == []

            # 进入到期前 30 分钟窗口，自动续约并换取新令牌
            now = 3200.0
            assert await auth_service.prepare_reauthorization(now=3200.0) is True
            assert seen_urls == ["https://login.test/authorize"]
            assert store.load().access_token.get_secret_value() == "renewed-token"

            summary = await auth_service.status_summary(now=3200.0)
            assert summary.logged_in is True
            assert summary.reauth_recommended is False
            assert summary.reauth_lead_seconds == 1800
            assert summary.auto_renew_enabled is True
            assert summary.last_auto_renew_status == "succeeded"
            assert summary.last_auto_renew_error is None

    asyncio.run(run())


def test_auto_renew_failure_keeps_pending_url_and_respects_cooldown(tmp_path, monkeypatch) -> None:
    """验证自动续约遇风控/失败时保留人工授权链接，并遵守冷却期与最大重试次数。"""

    async def run() -> None:
        now = 3200.0
        monkeypatch.setattr("src.clients.jd_gold.time.time", lambda: now)
        store = JDGoldAuthRepository(tmp_path / "jd_auth.sqlite3", scope="https://bff.test")
        store.save(JDGoldAuthState(access_token=SecretStr("old-token"), expires_at=4700.0))

        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path.rsplit("/", 1)[-1]
            if path == "getLoginUrl":
                return httpx.Response(200, json={"code": 200, "data": {"authorizeUrl": "https://login.test/authorize"}})
            pytest.fail(f"意外上游请求：{path}")

        attempts = 0

        class _FailingAuthorizer:
            async def authorize(self, authorize_url: str, complete_callback, *, headless=None) -> None:
                nonlocal attempts
                attempts += 1
                raise JDGoldBrowserRenewError("CAPTCHA_OR_MFA_REQUIRED", "触发验证码，已停止自动续约")

            async def interactive_login(self, authorize_url: str, complete_callback, *, timeout_seconds=300.0) -> None:
                await complete_callback()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = JDGoldClient(http, auth_store=store)
            auth_service = JDGoldAuthService(
                client,
                store,
                enabled=False,
                browser_authorizer=_FailingAuthorizer(),
                auto_renew_enabled=True,
                reauth_lead_seconds=1800,
                auto_renew_cooldown_seconds=180.0,
                auto_renew_max_attempts=2,
            )
            assert await auth_service.prepare_reauthorization(now=3200.0) is True
            assert attempts == 1
            summary = await auth_service.status_summary(now=3200.0)
            assert summary.pending_login is True
            assert summary.authorize_url == "https://login.test/authorize"
            assert summary.last_auto_renew_status == "failed"
            assert summary.last_auto_renew_error == "触发验证码，已停止自动续约"

            # 冷却期内不重复打开浏览器
            assert await auth_service.prepare_reauthorization(now=3300.0) is False
            assert attempts == 1

            # 超过冷却期允许第 2 次尝试，达到上限后停止重试
            assert await auth_service.prepare_reauthorization(now=3400.0) is False
            assert attempts == 2
            assert await auth_service.prepare_reauthorization(now=3700.0) is False
            assert attempts == 2

    asyncio.run(run())


def test_jd_auth_renew_api_endpoint_success_and_error_mapping(tmp_path) -> None:
    """验证手动触发自动续约接口的成功响应与错误状态码映射。"""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "getLoginUrl":
            return httpx.Response(200, json={"code": 200, "data": {"authorizeUrl": "https://login.test/authorize"}})
        if path == "exchange":
            return httpx.Response(
                200,
                json={"code": 200, "data": {"accessToken": "auto-token", "expiresIn": 3_600_000}},
            )
        if path == "queryHoldings":
            return httpx.Response(
                200,
                json={"code": 200, "data": {"totalGramAll": 2.0, "avgCostPrice": 600.0, "holdingList": []}},
            )
        pytest.fail(f"意外上游请求：{path}")

    settings = Settings.model_construct(
        data_dir=str(tmp_path),
        timesfm_enabled=False,
        news_enabled=False,
        live_quotes_enabled=False,
        market_bars_enabled=False,
        news_sync_enabled=False,
        jd_holdings_sync_enabled=False,
        jd_proactive_auth_enabled=False,
        jd_auto_renew_enabled=True,
        jd_access_token=SecretStr(""),
    )
    runtime = RuntimeDependencies(settings)
    asyncio.run(runtime.jd_http.aclose())
    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    runtime.jd_http = mock_http
    runtime.jd_gold = JDGoldGateway(mock_http, settings)
    runtime.jd_holdings._gateway = runtime.jd_gold

    class _ModeAuthorizer:
        def __init__(self) -> None:
            self.error: JDGoldBrowserRenewError | None = None

        async def authorize(self, authorize_url: str, complete_callback, *, headless=None) -> None:
            if self.error is not None:
                raise self.error
            await complete_callback()

        async def interactive_login(self, authorize_url: str, complete_callback, *, timeout_seconds=300.0) -> None:
            await complete_callback()

    authorizer = _ModeAuthorizer()
    runtime.jd_gold.auth._browser_authorizer = authorizer
    runtime.jd_gold.auth._auto_renew_enabled = True
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: runtime

    try:
        client = TestClient(app)
        ok_resp = client.post("/api/v1/jd/auth/renew")
        assert ok_resp.status_code == 200
        body = ok_resp.json()
        assert body["ok"] is True
        assert body["status"]["logged_in"] is True
        assert body["status"]["last_auto_renew_status"] == "succeeded"

        authorizer.error = JDGoldBrowserRenewError("CAPTCHA_OR_MFA_REQUIRED", "需人工完成验证码")
        assert client.post("/api/v1/jd/auth/renew").status_code == 409

        authorizer.error = JDGoldBrowserRenewError("CDP_UNAVAILABLE", "CDP 未开启")
        assert client.post("/api/v1/jd/auth/renew").status_code == 503

        authorizer.error = JDGoldBrowserRenewError("RENEW_TIMEOUT", "续约超时")
        assert client.post("/api/v1/jd/auth/renew").status_code == 504
    finally:
        app.dependency_overrides.clear()
        asyncio.run(runtime.close())


def test_browser_authorizer_cdp_auto_login_and_captcha_guard() -> None:
    """验证浏览器自动授权客户端在 CDP 模式下自动填表登录、仅关闭新建标签页以及遇验证码立即中止。"""

    class _FakeLocator:
        def __init__(self, page: "_FakePage", selector: str) -> None:
            self._page = page
            self._selector = selector

        @property
        def first(self) -> "_FakeLocator":
            return self

        async def is_visible(self, *, timeout: int = 500) -> bool:
            return self._selector in self._page.visible_selectors

        async def click(self, *, timeout: int = 3000) -> None:
            self._page.clicked.append(self._selector)
            if self._selector == "button#loginSubmit.user-is-login":
                if "button#agreeBtn" not in self._page.clicked:
                    self._page.visible_selectors = {"button#agreeBtn", "button#loginSubmit.user-is-login"}
                else:
                    self._page.url = "https://oauth.jd.com/authorize"
                    self._page.visible_selectors = {"button:has-text('确认授权')"}
            elif self._selector == "button#agreeBtn":
                self._page.visible_selectors = {"button#loginSubmit.user-is-login"}
            elif self._selector == "#loginsubmit":
                self._page.url = "https://oauth.jd.com/authorize"
                self._page.visible_selectors = {"button:has-text('确认授权')"}
            elif self._selector == "button:has-text('确认授权')":
                self._page.authorized = True

        async def fill(self, value: str, *, timeout: int = 3000) -> None:
            self._page.filled[self._selector] = value

    class _FakePage:
        def __init__(self, *, initial_url: str, visible: set[str]) -> None:
            self.url = initial_url
            self.visible_selectors = set(visible)
            self.clicked: list[str] = []
            self.filled: dict[str, str] = {}
            self.authorized = False
            self.closed = False

        async def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 10000) -> None:
            return None

        def locator(self, selector: str) -> _FakeLocator:
            return _FakeLocator(self, selector)

        async def close(self) -> None:
            self.closed = True

    class _FakeContext:
        def __init__(self, page: _FakePage) -> None:
            self._page = page
            self.closed = False

        async def new_page(self) -> _FakePage:
            return self._page

        async def close(self) -> None:
            self.closed = True

    class _FakeBrowser:
        def __init__(self, context: _FakeContext) -> None:
            self.contexts = [context]
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    def make_factory(page: _FakePage) -> tuple[object, _FakeBrowser, _FakeContext]:
        context = _FakeContext(page)
        browser = _FakeBrowser(context)

        class _FakeChromium:
            async def connect_over_cdp(self, url: str, *, timeout: int = 15000) -> _FakeBrowser:
                return browser

        class _FakePlaywrightCM:
            async def __aenter__(self):
                return type("_PW", (), {"chromium": _FakeChromium()})()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return (lambda: _FakePlaywrightCM()), browser, context

    async def run() -> None:
        # 1. CDP 遇登录页自动填充账号密码 -> 跳转授权页点击确认 -> 兑换令牌 -> 仅关闭新建标签页
        page = _FakePage(
            initial_url="https://passport.jd.com/new/login.aspx",
            visible={"#loginname", "#nloginpwd", "#loginsubmit"},
        )
        factory, browser, context = make_factory(page)
        authorizer = JDGoldBrowserAuthorizer(
            cdp_url="http://127.0.0.1:9222",
            username=SecretStr("demo_user"),
            password=SecretStr("demo_pass"),
            timeout_seconds=5.0,
            poll_interval_seconds=0.01,
            playwright_factory=factory,  # type: ignore[arg-type]
        )

        async def complete_cb() -> None:
            if not page.authorized:
                raise JDGoldError("PENDING", "尚未确认授权")

        await authorizer.authorize("https://oauth.jd.com/authorize", complete_cb)
        assert page.filled == {"#loginname": "demo_user", "#nloginpwd": "demo_pass"}
        assert "button:has-text('确认授权')" in page.clicked
        assert page.closed is True
        assert context.closed is False
        assert browser.closed is False

        # 2. 遇到验证码立即中止，绝不绕过
        captcha_page = _FakePage(
            initial_url="https://passport.jd.com/new/login.aspx",
            visible={"#JDJRV-wrap-loginsubmit"},
        )
        captcha_factory, _, _ = make_factory(captcha_page)
        captcha_authorizer = JDGoldBrowserAuthorizer(
            cdp_url="http://127.0.0.1:9222",
            username=SecretStr("demo_user"),
            password=SecretStr("demo_pass"),
            timeout_seconds=5.0,
            poll_interval_seconds=0.01,
            playwright_factory=captcha_factory,  # type: ignore[arg-type]
        )
        with pytest.raises(JDGoldBrowserRenewError) as captcha_err:
            await captcha_authorizer.authorize("https://oauth.jd.com/authorize", complete_cb)
        assert captcha_err.value.code == "CAPTCHA_OR_MFA_REQUIRED"
        assert captcha_page.closed is True

        # 3. 已登录会话快捷确认（user-is-login）不触发填表，直接一键确认授权
        quick_page = _FakePage(
            initial_url="https://passport.jd.com/oauth/login",
            visible={"button#loginSubmit.user-is-login"},
        )
        quick_factory, _, _ = make_factory(quick_page)
        quick_authorizer = JDGoldBrowserAuthorizer(
            cdp_url="http://127.0.0.1:9222",
            timeout_seconds=5.0,
            poll_interval_seconds=0.01,
            playwright_factory=quick_factory,  # type: ignore[arg-type]
        )

        async def quick_complete_cb() -> None:
            if not quick_page.authorized:
                raise JDGoldError("PENDING", "尚未确认授权")

        await quick_authorizer.authorize("https://oauth.jd.com/authorize", quick_complete_cb)
        assert quick_page.filled == {}
        assert quick_page.clicked == [
            "button#loginSubmit.user-is-login",
            "button#agreeBtn",
            "button#loginSubmit.user-is-login",
            "button:has-text('确认授权')",
        ]
        assert quick_page.closed is True

        # 4. 拒绝非法非京东钓鱼域名
        with pytest.raises(JDGoldBrowserRenewError) as invalid_err:
            await authorizer.authorize("https://evil.example/phish", complete_cb)
        assert invalid_err.value.code == "INVALID_URL"

    asyncio.run(run())


def test_browser_authorizer_persistent_profile_and_interactive_session(tmp_path) -> None:
    """验证持久化浏览器 Profile 的 0700 权限目录创建与交互式会话保存。"""
    profile_dir = tmp_path / "jd-browser-profile"
    launched_calls: list[tuple[str, bool]] = []

    class _FakePage:
        def __init__(self) -> None:
            self.url = "https://oauth.jd.com/authorize"
            self.closed = False

        async def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 30000) -> None:
            return None

        def is_closed(self) -> bool:
            return self.closed

    class _FakePersistentContext:
        def __init__(self) -> None:
            self.pages = [_FakePage()]
            self.closed = False

        async def new_page(self) -> _FakePage:
            return self.pages[0]

        async def close(self) -> None:
            self.closed = True

    ctx = _FakePersistentContext()

    class _FakeChromium:
        async def launch_persistent_context(
            self,
            user_data_dir: str,
            *,
            channel: str | None = None,
            headless: bool = True,
        ) -> _FakePersistentContext:
            launched_calls.append((user_data_dir, headless))
            return ctx

    class _FakePlaywrightCM:
        async def __aenter__(self):
            return type("_PW", (), {"chromium": _FakeChromium()})()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def run() -> None:
        authorizer = JDGoldBrowserAuthorizer(
            cdp_url="",
            profile_dir=profile_dir,
            poll_interval_seconds=0.01,
            playwright_factory=lambda: _FakePlaywrightCM(),
        )
        attempts = 0

        async def complete_cb() -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise JDGoldError("PENDING", "等待人工确认")

        await authorizer.interactive_login("https://oauth.jd.com/authorize", complete_cb, timeout_seconds=5.0)
        assert profile_dir.is_dir()
        assert stat.S_IMODE(profile_dir.stat().st_mode) == 0o700
        assert launched_calls == [(str(profile_dir), False)]
        assert ctx.closed is True

    asyncio.run(run())
