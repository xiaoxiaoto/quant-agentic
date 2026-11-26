"""京东账户授权管理 RESTful 请求与响应数据模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class JDAuthStatusResponse(BaseModel):
    """京东账户当前授权与待确认会话状态摘要。"""

    logged_in: bool = Field(description="当前是否存在有效京东访问令牌")
    usable_seconds: int | None = Field(default=None, description="距离令牌真实到期的剩余秒数")
    expires_at: datetime | None = Field(default=None, description="令牌真实到期时间（UTC）")
    reauth_recommended: bool = Field(default=False, description="是否已进入到期前建议重新授权窗口")
    reauth_lead_seconds: int = Field(default=1800, description="到期前自动续约提前窗口（秒）")
    refresh_supported: bool = Field(default=False, description="上游是否支持静默续期")
    pending_login: bool = Field(default=False, description="是否存在未过期的待确认登录会话")
    pending_login_expires_at: datetime | None = Field(default=None, description="待确认登录会话过期时间（UTC）")
    authorize_url: str | None = Field(default=None, description="当前待确认会话对应的京东浏览器授权链接")
    auto_renew_enabled: bool = Field(default=False, description="是否已启用到期前浏览器自动续约")
    auto_renew_in_progress: bool = Field(default=False, description="是否正在执行浏览器自动续约")
    last_auto_renew_at: datetime | None = Field(default=None, description="最近一次尝试自动续约时间（UTC）")
    last_auto_renew_status: Literal["idle", "running", "succeeded", "failed"] = Field(
        default="idle",
        description="最近一次自动续约执行状态",
    )
    last_auto_renew_error: str | None = Field(default=None, description="最近一次自动续约失败原因摘要")


class JDAuthLoginResponse(BaseModel):
    """发起京东浏览器授权登录的响应体。"""

    authorize_url: str = Field(description="京东官方浏览器授权链接")
    expires_at: datetime | None = Field(default=None, description="待确认授权会话过期时间（UTC）")
    message: str = Field(description="下一步操作提示")


class JDAuthActionResponse(BaseModel):
    """完成授权、权限验证或退出登录的统一响应体。"""

    ok: bool = Field(description="操作是否成功")
    message: str = Field(description="操作结果说明")
    status: JDAuthStatusResponse = Field(description="操作完成后的最新授权状态")
