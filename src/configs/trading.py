"""交易、费率与行情门禁配置。"""

from pydantic import Field
from pydantic_settings import BaseSettings


class TradingSettings(BaseSettings):
    """交易费率、默认参数、存储路径与行情异常监控阈值配置。"""

    data_dir: str = "data"
    buy_fee_rate: float = Field(default=0.0, ge=0.0, lt=1.0, allow_inf_nan=False)
    sell_fee_rate: float = Field(default=0.004, ge=0.0, lt=1.0, allow_inf_nan=False)
    default_product_code: str = Field(default="GOLD_ACCUMULATION", min_length=1)
    default_trade_grams: float = Field(default=2.0, gt=0.0, allow_inf_nan=False)
    default_initial_cash: float = Field(default=100000.0, gt=0.0, allow_inf_nan=False)
    stale_quote_threshold_seconds: float = Field(default=30.0, gt=0.0, allow_inf_nan=False)
    spread_anomaly_threshold: float = Field(default=0.01, gt=0.0, lt=1.0, allow_inf_nan=False)
    synthetic_paper_quote_enabled: bool = False
    synthetic_paper_reference_product: str = Field(default="CMBC-JCJ", min_length=1)
    synthetic_paper_spread_pct: float = Field(default=0.002, gt=0.0, lt=0.05, allow_inf_nan=False)
    live_reference_assist_enabled: bool = False
    live_reference_spread_pct: float = Field(default=0.002, gt=0.0, lt=0.05, allow_inf_nan=False)
    basis_reference_product: str = Field(default="SGE-Au99.99", min_length=1)
    daily_loss_trigger_pct: float = Field(default=0.01, gt=0.0, lt=1.0, allow_inf_nan=False)
    weekly_loss_trigger_pct: float = Field(default=0.03, gt=0.0, lt=1.0, allow_inf_nan=False)
    signal_cooldown_seconds: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
