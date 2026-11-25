"""京东黄金授权与登录态会话服务。"""

import asyncio
import logging
import time
from contextlib import suppress

from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from src.clients.jd_gold import JDGoldClient, JDGoldError
from src.contracts.domains.jd_auth import JDGoldAuthState
from src.repositories.jd_gold_auth import JDGoldAuthRepository


class JDGoldAuthService:
    """负责京东黄金授权凭据持久化、自动重试与会话到期监听。"""

    def __init__(
        self,
        client: JDGoldClient,
        store: JDGoldAuthRepository,
        *,
        enabled: bool = True,
    ) -> None:
        """初始化授权服务与存储绑定。"""
        self._client = client
        self._store = store
        self._enabled = enabled
        self._task: asyncio.Task[None] | None = None
        self._requested_deadline: float | None = None

    async def _watch_expiry(self) -> None:
        """周期读取 SQLite，感知其他进程完成的新授权；失败时指数退避。"""
        logger = logging.getLogger(__name__)
        delay = 15.0
        while True:
            try:
                if await self.prepare_reauthorization():
                    logger.warning("京东黄金授权即将到期，请打开授权入口完成重新授权")
                delay = 15.0
            except JDGoldError as error:
                logger.warning("京东黄金提前发起授权失败（%s）", error.code)
                delay = min(delay * 2, 300.0)
            except (SQLAlchemyError, OSError, ValueError) as error:
                logger.warning("京东黄金授权状态读取或保存失败（%s）", type(error).__name__)
                delay = min(delay * 2, 300.0)
            await asyncio.sleep(delay)

    def auth_state(self) -> JDGoldAuthState:
        """同步读取最新授权状态。"""
        return self._store.load()

    async def auth_state_async(self) -> JDGoldAuthState:
        """异步读取最新授权状态，避免阻塞事件循环。"""
        return await asyncio.to_thread(self._store.load)

    async def prepare_reauthorization(self, now: float | None = None) -> bool:
        """在最后五分钟准备待确认会话，原凭据可用至实际到期。"""
        state = await asyncio.to_thread(self._store.load)
        deadline = state.expires_at
        actual_expiry = state.actual_expires_at
        current_time = now if now is not None else time.time()
        if (
            not state.access_token.get_secret_value()
            or deadline is None
            or actual_expiry is None
            or deadline == self._requested_deadline
            or not deadline <= current_time < actual_expiry
        ):
            return False
        try:
            await self._client.login()
        except JDGoldError as error:
            if error.code == "403":
                self._requested_deadline = deadline
            raise
        self._requested_deadline = deadline
        return True

    async def login(self) -> SecretStr:
        """主动发起授权登录，获取授权地址。"""
        return await self._client.login()

    async def complete_login(self) -> None:
        """确认完成浏览器授权并换取正式令牌。"""
        await self._client.complete_login()

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
