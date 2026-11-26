"""京东黄金授权与登录态会话服务。"""

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Literal

from pydantic import SecretStr

from src.clients.jd_gold import JDGoldClient, JDGoldError
from src.contracts.domains.jd_auth import (
    JD_GOLD_AUTO_RENEW_DEFAULT_LEAD_SECONDS,
    JDGoldAuthState,
    JDGoldBrowserAuthorizerProtocol,
    JDGoldBrowserRenewError,
)
from src.contracts.exceptions import StorageError
from src.contracts.schemas.jd_auth import JDAuthStatusResponse
from src.repositories.jd_gold_auth import JDGoldAuthRepository


class JDGoldAuthService:
    """负责京东黄金授权凭据持久化、到期前自动浏览器续约与会话监听。"""

    def __init__(
        self,
        client: JDGoldClient,
        store: JDGoldAuthRepository,
        *,
        enabled: bool = True,
        browser_authorizer: JDGoldBrowserAuthorizerProtocol | None = None,
        auto_renew_enabled: bool = False,
        reauth_lead_seconds: int = JD_GOLD_AUTO_RENEW_DEFAULT_LEAD_SECONDS,
        auto_renew_cooldown_seconds: float = 180.0,
        auto_renew_max_attempts: int = 3,
    ) -> None:
        """初始化授权服务、存储绑定与浏览器自动续约策略。"""
        self._client = client
        self._store = store
        self._enabled = enabled
        self._browser_authorizer = browser_authorizer
        self._auto_renew_enabled = auto_renew_enabled and browser_authorizer is not None
        self._reauth_lead_seconds = reauth_lead_seconds
        self._auto_renew_cooldown_seconds = auto_renew_cooldown_seconds
        self._auto_renew_max_attempts = auto_renew_max_attempts
        self._task: asyncio.Task[None] | None = None
        self._renew_lock = asyncio.Lock()
        self._requested_deadline: float | None = None
        self._renew_attempts_deadline: float | None = None
        self._renew_attempts: int = 0
        self._last_renew_attempt_ts: float | None = None
        self._auto_renew_in_progress: bool = False
        self._last_auto_renew_at: datetime | None = None
        self._last_auto_renew_status: Literal["idle", "running", "succeeded", "failed"] = "idle"
        self._last_auto_renew_error: str | None = None

    def _should_attempt_auto_renew(self, actual_expiry: float, current_time: float) -> bool:
        """判断当前到期窗口是否允许触发新一轮浏览器自动续约。"""
        if not self._auto_renew_enabled or self._browser_authorizer is None or self._auto_renew_in_progress:
            return False
        if self._renew_attempts_deadline != actual_expiry:
            return True
        if self._renew_attempts >= self._auto_renew_max_attempts:
            return False
        if self._last_renew_attempt_ts is None:
            return True
        return (current_time - self._last_renew_attempt_ts) >= self._auto_renew_cooldown_seconds

    async def _execute_browser_renew(
        self,
        *,
        headless: bool | None = None,
        now: float | None = None,
        tracked_deadline: float | None = None,
    ) -> None:
        """加锁执行浏览器自动续约并更新运行观测状态。"""
        if self._browser_authorizer is None:
            raise JDGoldBrowserRenewError("AUTO_RENEW_NOT_CONFIGURED", "未配置浏览器自动续约执行器")

        async with self._renew_lock:
            current_time = now if now is not None else time.time()
            if tracked_deadline is not None:
                if self._renew_attempts_deadline != tracked_deadline:
                    self._renew_attempts_deadline = tracked_deadline
                    self._renew_attempts = 0
                self._renew_attempts += 1
                self._last_renew_attempt_ts = current_time

            self._auto_renew_in_progress = True
            self._last_auto_renew_at = datetime.now(tz=UTC)
            self._last_auto_renew_status = "running"
            self._last_auto_renew_error = None
            try:
                authorize_url = await self._client.login()
                await self._browser_authorizer.authorize(
                    authorize_url.get_secret_value(),
                    self._client.complete_login,
                    headless=headless,
                )
            except JDGoldBrowserRenewError as error:
                self._last_auto_renew_status = "failed"
                self._last_auto_renew_error = error.message
                raise
            except JDGoldError as error:
                self._last_auto_renew_status = "failed"
                self._last_auto_renew_error = f"京东授权接口异常（{error.code}）"
                raise
            else:
                self._last_auto_renew_status = "succeeded"
                self._last_auto_renew_error = None
                self._requested_deadline = None
                self._renew_attempts_deadline = None
                self._renew_attempts = 0
                self._last_renew_attempt_ts = None
            finally:
                self._auto_renew_in_progress = False

    async def _watch_expiry(self) -> None:
        """周期读取 SQLite，在到期前窗口自动续约或准备重授权；失败时指数退避。"""
        logger = logging.getLogger(__name__)
        delay = 15.0
        while True:
            try:
                if await self.prepare_reauthorization():
                    if self._last_auto_renew_status == "succeeded":
                        logger.info("京东黄金授权已通过浏览器自动续约完成")
                    else:
                        logger.warning("京东黄金授权即将到期，请打开授权入口完成重新授权")
                delay = 15.0
            except JDGoldError as error:
                logger.warning("京东黄金提前发起授权失败（%s）", error.code)
                delay = min(delay * 2, 300.0)
            except (StorageError, OSError, ValueError) as error:
                logger.warning("京东黄金授权状态读取或保存失败（%s）", type(error).__name__)
                delay = min(delay * 2, 300.0)
            await asyncio.sleep(delay)

    def auth_state(self) -> JDGoldAuthState:
        """同步读取最新授权状态。"""
        return self._store.load()

    async def auth_state_async(self) -> JDGoldAuthState:
        """异步读取最新授权状态，避免阻塞事件循环。"""
        return await asyncio.to_thread(self._store.load)

    async def status_summary(self, now: float | None = None) -> JDAuthStatusResponse:
        """生成面向接口与工作台展示的脱敏授权状态摘要。"""
        state = await self.auth_state_async()
        current_time = now if now is not None else time.time()
        actual_expiry = state.actual_expires_at
        remaining = max(0, int(actual_expiry - current_time)) if actual_expiry is not None else None
        logged_in = bool(state.access_token.get_secret_value()) and (remaining is None or remaining > 0)
        reauth_recommended = bool(
            logged_in and actual_expiry is not None and current_time >= actual_expiry - self._reauth_lead_seconds
        )
        pending_login = bool(state.login_state) and state.login_expires_at > current_time
        authorize_url = state.login_url.get_secret_value() if pending_login else None
        return JDAuthStatusResponse(
            logged_in=logged_in,
            usable_seconds=remaining if logged_in else None,
            expires_at=datetime.fromtimestamp(actual_expiry, tz=UTC)
            if logged_in and actual_expiry is not None
            else None,
            reauth_recommended=reauth_recommended,
            reauth_lead_seconds=self._reauth_lead_seconds,
            refresh_supported=self._auto_renew_enabled,
            pending_login=pending_login,
            pending_login_expires_at=datetime.fromtimestamp(state.login_expires_at, tz=UTC) if pending_login else None,
            authorize_url=authorize_url or None,
            auto_renew_enabled=self._auto_renew_enabled,
            auto_renew_in_progress=self._auto_renew_in_progress,
            last_auto_renew_at=self._last_auto_renew_at,
            last_auto_renew_status=self._last_auto_renew_status,
            last_auto_renew_error=self._last_auto_renew_error,
        )

    async def prepare_reauthorization(self, now: float | None = None) -> bool:
        """在到期前窗口准备待确认会话，并在启用自动续约时通过浏览器完成续约。"""
        logger = logging.getLogger(__name__)
        state = await asyncio.to_thread(self._store.load)
        actual_expiry = state.actual_expires_at
        current_time = now if now is not None else time.time()
        if (
            not state.access_token.get_secret_value()
            or actual_expiry is None
            or not (actual_expiry - self._reauth_lead_seconds) <= current_time < actual_expiry
        ):
            return False

        if self._renew_lock.locked():
            return False

        if self._should_attempt_auto_renew(actual_expiry, current_time):
            first_prepare = actual_expiry != self._requested_deadline
            try:
                await self._execute_browser_renew(now=current_time, tracked_deadline=actual_expiry)
                return True
            except JDGoldError as error:
                if error.code == "403":
                    self._requested_deadline = actual_expiry
                raise
            except JDGoldBrowserRenewError as error:
                self._requested_deadline = actual_expiry
                logger.warning("京东黄金到期前自动续约失败（%s）：%s", error.code, error.message)
                return first_prepare

        if actual_expiry != self._requested_deadline:
            async with self._renew_lock:
                try:
                    await self._client.login()
                except JDGoldError as error:
                    if error.code == "403":
                        self._requested_deadline = actual_expiry
                    raise
                self._requested_deadline = actual_expiry
                return True

        return False

    async def auto_renew(self, *, headless: bool | None = None, now: float | None = None) -> None:
        """手动或调度触发一次浏览器自动续约流程。"""
        await self._execute_browser_renew(headless=headless, now=now)

    async def interactive_browser_login(self, *, timeout_seconds: float = 300.0) -> None:
        """打开有头持久化浏览器完成首次人工登录与授权，将 Cookie 会话和令牌持久化到本地。"""
        if self._browser_authorizer is None:
            raise JDGoldBrowserRenewError("AUTO_RENEW_NOT_CONFIGURED", "未配置浏览器自动续约执行器")
        async with self._renew_lock:
            self._auto_renew_in_progress = True
            self._last_auto_renew_at = datetime.now(tz=UTC)
            self._last_auto_renew_status = "running"
            self._last_auto_renew_error = None
            try:
                authorize_url = await self._client.login()
                await self._browser_authorizer.interactive_login(
                    authorize_url.get_secret_value(),
                    self._client.complete_login,
                    timeout_seconds=timeout_seconds,
                )
            except JDGoldBrowserRenewError as error:
                self._last_auto_renew_status = "failed"
                self._last_auto_renew_error = error.message
                raise
            except JDGoldError as error:
                self._last_auto_renew_status = "failed"
                self._last_auto_renew_error = f"京东授权接口异常（{error.code}）"
                raise
            else:
                self._last_auto_renew_status = "succeeded"
                self._last_auto_renew_error = None
                self._requested_deadline = None
                self._renew_attempts_deadline = None
                self._renew_attempts = 0
                self._last_renew_attempt_ts = None
            finally:
                self._auto_renew_in_progress = False

    async def login(self) -> SecretStr:
        """主动发起授权登录，获取授权地址。"""
        return await self._client.login()

    async def complete_login(self) -> None:
        """确认完成浏览器授权并换取正式令牌。"""
        await self._client.complete_login()

    async def verify(self) -> None:
        """调用京东持仓接口实测当前授权凭据是否有效。"""
        await self._client.holdings()

    async def logout(self) -> None:
        """退出京东登录并清除本地持久化凭据。"""
        self._requested_deadline = None
        self._renew_attempts_deadline = None
        self._renew_attempts = 0
        self._last_renew_attempt_ts = None
        self._last_auto_renew_status = "idle"
        self._last_auto_renew_error = None
        await self._client.logout()

    async def start(self) -> None:
        """启动应用级授权检查任务。"""
        if self._enabled and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._watch_expiry())

    async def stop(self) -> None:
        """在 HTTP 连接关闭前停止授权检查。"""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
