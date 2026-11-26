"""京东授权持久化数据与存储契约。"""

import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from pydantic import BaseModel, FiniteFloat, SecretStr

JD_GOLD_REAUTH_LEAD_SECONDS = 300
JD_GOLD_AUTO_RENEW_DEFAULT_LEAD_SECONDS = 1800


class JDGoldAuthState(BaseModel):
    """时间均为 Unix 秒，便于跨进程恢复且不暴露凭据。"""

    access_token: SecretStr = SecretStr("")
    expires_at: FiniteFloat | None = None  # 历史字段保存的是提前重新授权的时刻。
    expired_token_fingerprint: str | None = None
    login_state: str = ""
    login_verifier: SecretStr = SecretStr("")
    login_url: SecretStr = SecretStr("")
    login_expires_at: FiniteFloat = 0

    @property
    def credential_fingerprint(self) -> str | None:
        """供本地只读快照识别同一份有效或过期授权。"""
        token = self.access_token.get_secret_value()
        return hashlib.sha256(token.encode()).hexdigest() if token else self.expired_token_fingerprint

    @property
    def actual_expires_at(self) -> float | None:
        """返回上游令牌真实到期时间戳（Unix 秒），避免调用方漏加提前续期窗口。"""
        return self.expires_at + JD_GOLD_REAUTH_LEAD_SECONDS if self.expires_at is not None else None

    def is_authorized(self, now: float | None = None) -> bool:
        """判断当前授权令牌是否非空且尚未超过真实到期时间。"""
        if not self.access_token.get_secret_value():
            return False
        expiry = self.actual_expires_at
        if expiry is None:
            return True
        current_time = now if now is not None else time.time()
        return current_time < expiry


class JDGoldAuthStore(Protocol):
    """由运行时注入存储，客户端不依赖仓储层。"""

    def load(self) -> JDGoldAuthState:
        """读取已持久化的授权状态。"""
        ...

    def save(self, state: JDGoldAuthState) -> None:
        """原子保存授权状态。"""
        ...


class JDGoldBrowserRenewError(RuntimeError):
    """浏览器自动续约失败异常，仅携带脱敏错误码与可读摘要。"""

    def __init__(self, code: str, message: str) -> None:
        """初始化脱敏续约异常。"""
        super().__init__(message)
        self.code = code
        self.message = message


class JDGoldBrowserAuthorizerProtocol(Protocol):
    """京东浏览器自动授权执行契约，供服务层依赖注入与测试替身使用。"""

    async def authorize(
        self,
        authorize_url: str,
        complete_callback: Callable[[], Awaitable[None]],
        *,
        headless: bool | None = None,
    ) -> None:
        """打开京东授权页并完成确认授权与令牌兑换。"""
        ...

    async def interactive_login(
        self,
        authorize_url: str,
        complete_callback: Callable[[], Awaitable[None]],
        *,
        timeout_seconds: float = 300.0,
    ) -> None:
        """打开有头持久化浏览器窗口供人工完成一次性登录与授权，并保存会话。"""
        ...
