"""机器学习与大模型推理运行配置。"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


class ModelSettings(BaseSettings):
    """数值预测模型、时序基础模型与新闻风险大模型配置。"""

    lightgbm_model_dir: str | None = None
    timesfm_enabled: bool = True
    timesfm_checkpoint: str = "google/timesfm-3.0-pytorch"
    timesfm_device: str = "auto"
    timesfm_context_length: int = Field(default=512, ge=32, le=15360)
    news_llm_enabled: bool = True
    news_llm_base_url: str = "https://api.openai.com/v1"
    news_llm_model: str = ""
    news_llm_api_key: SecretStr = SecretStr("")
    news_llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300, allow_inf_nan=False)
