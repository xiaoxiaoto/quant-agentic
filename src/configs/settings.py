"""统一聚合配置中心。"""

from pydantic_settings import SettingsConfigDict

from src.configs.app import AppSettings
from src.configs.model import ModelSettings
from src.configs.server import ServerSettings
from src.configs.trading import TradingSettings


class Settings(AppSettings, ServerSettings, TradingSettings, ModelSettings):
    """整合各领域配置组的统一应用配置对象。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
