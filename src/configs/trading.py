"""交易、费率与行情门禁配置。"""

from pydantic_settings import BaseSettings


class TradingSettings(BaseSettings):
    """交易费率、存储路径与行情异常监控阈值配置。"""

    data_dir: str = "data/quotes"
    buy_fee_rate: float = 0.0
    sell_fee_rate: float = 0.004
    stale_quote_threshold_seconds: float = 30.0
    spread_anomaly_threshold: float = 0.01
