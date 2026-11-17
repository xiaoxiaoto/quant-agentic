from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    name: str = "quant-agentic"


class ServerSettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False


class TradingSettings(BaseSettings):
    data_dir: str = "data/quotes"
    buy_fee_rate: float = 0.0
    sell_fee_rate: float = 0.004
    stale_quote_threshold_seconds: float = 30.0
    spread_anomaly_threshold: float = 0.01


class Settings(AppSettings, ServerSettings, TradingSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
