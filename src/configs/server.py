"""HTTP 与网络服务运行配置。"""

from pydantic_settings import BaseSettings


class ServerSettings(BaseSettings):
    """服务网络监听与调试模式配置。"""

    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
