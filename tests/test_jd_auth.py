"""京东授权的 SQLite 恢复、失效清理与权限测试。"""

import asyncio
import json
import sqlite3
import stat
from contextlib import closing

import httpx
import pytest
from pydantic import SecretStr

from src.clients.jd_gold import JDGoldClient, JDGoldError, JDGoldLoginRequiredError
from src.configs.settings import Settings
from src.contracts.domains.jd_auth import JDGoldAuthState
from src.repositories.jd_gold_auth import JDGoldAuthRepository
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
        settings = Settings(data_dir=str(tmp_path), jd_access_token=SecretStr(""), jd_proactive_auth_enabled=True)
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
