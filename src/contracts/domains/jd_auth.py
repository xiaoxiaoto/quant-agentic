"""京东授权持久化数据与存储契约。"""

from typing import Protocol

from pydantic import BaseModel, FiniteFloat, SecretStr

JD_GOLD_REAUTH_LEAD_SECONDS = 300


class JDGoldAuthState(BaseModel):
    """时间均为 Unix 秒，便于跨进程恢复且不暴露凭据。"""

    access_token: SecretStr = SecretStr("")
    expires_at: FiniteFloat | None = None  # 历史字段保存的是提前重新授权的时刻。
    login_state: str = ""
    login_verifier: SecretStr = SecretStr("")
    login_url: SecretStr = SecretStr("")
    login_expires_at: FiniteFloat = 0

    @property
    def actual_expires_at(self) -> float | None:
        """返回上游令牌真实到期时间戳（Unix 秒），避免调用方漏加提前续期窗口。"""
        return self.expires_at + JD_GOLD_REAUTH_LEAD_SECONDS if self.expires_at is not None else None


class JDGoldAuthStore(Protocol):
    """由运行时注入存储，客户端不依赖仓储层。"""

    def load(self) -> JDGoldAuthState:
        """读取已持久化的授权状态。"""
        ...

    def save(self, state: JDGoldAuthState) -> None:
        """原子保存授权状态。"""
        ...
