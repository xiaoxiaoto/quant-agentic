"""机器学习与大模型推理运行配置。"""

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings


class ModelSettings(BaseSettings):
    """数值预测模型、时序基础模型与新闻风险大模型配置。"""

    lightgbm_model_dir: str | None = None
    timesfm_enabled: bool = True
    timesfm_checkpoint: str = "google/timesfm-3.0-pytorch"
    timesfm_device: Literal["auto", "cpu", "mps", "cuda"] = "auto"
    timesfm_context_length: int = Field(default=512, ge=32, le=15360)

    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices("openai_base_url", "OPENAI_BASE_URL", "OPENAI_API_BASE"),
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("openai_model", "OPENAI_MODEL", "OPENAI_MODEL_NAME"),
    )
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("openai_api_key", "OPENAI_API_KEY"),
    )
    openai_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=300,
        allow_inf_nan=False,
        validation_alias=AliasChoices("openai_timeout_seconds", "OPENAI_TIMEOUT_SECONDS", "OPENAI_TIMEOUT"),
    )

    news_enabled: bool = Field(default=True, validation_alias=AliasChoices("news_enabled", "NEWS_ENABLED"))
    news_sync_enabled: bool = False
    news_sync_interval_seconds: float = Field(default=300.0, ge=30.0, le=3600.0, allow_inf_nan=False)
    news_sync_page_size: int = Field(default=10, ge=1, le=50)

    @field_validator("lightgbm_model_dir", mode="before")
    @classmethod
    def _normalize_empty_model_dir(cls, value: object) -> object:
        """将空白字符串环境变量归一化为 None。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value
