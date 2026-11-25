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
    jd_timeout_seconds: float = Field(default=10.0, gt=0, le=60, allow_inf_nan=False)
    live_quotes_enabled: bool = True
    live_quotes_interval_seconds: float = Field(default=10.0, ge=5, le=300, allow_inf_nan=False)
    live_quotes_products: tuple[str, ...] | None = None
