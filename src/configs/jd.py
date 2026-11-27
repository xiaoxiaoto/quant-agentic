"""京东客户端及实时参考行情配置。"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


class JDSettings(BaseSettings):
    """接口域名、调用身份与采集周期由统一配置注入。"""

    jd_bff_base_url: str = "https://ms.jr.jd.com/gw2/generic/cfGoldAi/pc/m"
    jd_public_base_url: str = "https://ms.jr.jd.com/gw2/generic/ugActs/h5/m"
    jd_gateway_base_url: str = "https://youqian.jd.com/api/gateway"
    jd_latest_price_url: str = "https://api.jdjygold.com/gw/generic/hj/h5/m/latestPrice"
    jd_access_token: SecretStr = SecretStr("")
    jd_gateway_api_key: SecretStr = SecretStr("")
    jd_claw: str = ""
    jd_proactive_auth_enabled: bool = True
    jd_auto_renew_enabled: bool = True
    jd_auto_renew_lead_seconds: int = Field(default=1800, ge=60, le=7200)
    jd_auto_renew_cdp_url: str = ""
    jd_auto_renew_headless: bool = True
    jd_auto_renew_profile_dir: str = ""
    jd_auto_renew_timeout_seconds: float = Field(default=45.0, ge=10.0, le=300.0, allow_inf_nan=False)
    jd_auto_renew_cooldown_seconds: float = Field(default=180.0, ge=30.0, le=1800.0, allow_inf_nan=False)
    jd_auto_renew_max_attempts: int = Field(default=3, ge=1, le=10)
    jd_login_username: SecretStr = SecretStr("")
    jd_login_password: SecretStr = SecretStr("")
    jd_auto_renew_confirm_selectors: tuple[str, ...] = (
        "button:has-text('确认授权')",
        "button:has-text('同意授权')",
        "button:has-text('授权登录')",
        "button:has-text('立即授权')",
        "button:has-text('允许')",
        "a:has-text('确认授权')",
        "a:has-text('同意授权')",
    )
    jd_timeout_seconds: float = Field(default=10.0, gt=0, le=60, allow_inf_nan=False)
    live_quotes_enabled: bool = True
    live_quotes_interval_seconds: float = Field(default=10.0, ge=5, le=300, allow_inf_nan=False)
    live_quotes_products: tuple[str, ...] | None = None
    jd_holdings_sync_enabled: bool = True
    jd_holdings_sync_interval_seconds: float = Field(default=60.0, ge=10, le=3600, allow_inf_nan=False)
    market_bars_enabled: bool = True
    market_bars_interval_seconds: float = Field(default=60.0, ge=10, le=3600, allow_inf_nan=False)
    market_kline_products: tuple[str, ...] = ("CMBC-JCJ", "SGE-Au99.99", "WG-JDAU", "WG-XAUUSD")
    market_kline_intervals: tuple[str, ...] = ("m1", "m5", "m15", "m30", "m60", "day", "week", "month", "year")
    market_timesharing_intervals: tuple[str, ...] = ("m1", "m5")
