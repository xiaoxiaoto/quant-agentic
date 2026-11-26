"""多周期 K 线与分时行情采集领域模型。"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, FiniteFloat


class KLineBar(BaseModel):
    """单根多周期 K 线行情记录（非可成交双边报价）。"""

    product_code: str
    interval: str
    source: Literal["jd_public_kline", "jd_sim_kline"]
    bar_time: AwareDatetime
    trade_date: str
    open_price: FiniteFloat = Field(gt=0)
    high_price: FiniteFloat = Field(gt=0)
    low_price: FiniteFloat = Field(gt=0)
    close_price: FiniteFloat = Field(gt=0)
    pre_close: FiniteFloat | None = None
    volume: FiniteFloat | None = None
    turnover: FiniteFloat | None = None
    collected_at: AwareDatetime


class TimeSharingPoint(BaseModel):
    """单条分时走势点位记录。"""

    product_code: str
    granularity: str
    source: Literal["jd_sim_time_sharing"] = "jd_sim_time_sharing"
    trade_time: AwareDatetime
    show_time: str
    last_price: FiniteFloat = Field(gt=0)
    pre_close: FiniteFloat | None = None
    change_percent: FiniteFloat | None = None
    trade_volume: FiniteFloat | None = None
    total_volume: FiniteFloat | None = None
    turnover: FiniteFloat | None = None
    collected_at: AwareDatetime


class MarketBarCollectorStatus(BaseModel):
    """多周期 K 线与分时采集器运行状态。"""

    enabled: bool
    running: bool
    interval_seconds: float
    kline_bars_saved: int
    timesharing_points_saved: int
    failures: int
    last_attempt_at: AwareDatetime | None
    last_success_at: AwareDatetime | None
    last_error: str | None
    auth_available: bool
