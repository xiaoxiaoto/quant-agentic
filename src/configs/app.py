"""基础应用设置。"""

from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    """应用基础元信息配置。"""

    name: str = "quant-agentic"
