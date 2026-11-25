"""HTTP 与网络服务运行配置。"""

from pydantic import Field
from pydantic_settings import BaseSettings


class ServerSettings(BaseSettings):
    """服务网络监听与调试模式配置。"""

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    debug: bool = False
