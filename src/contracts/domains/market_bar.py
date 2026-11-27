"""多周期 K 线与分时行情采集领域模型。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, NamedTuple

from pydantic import AwareDatetime, BaseModel, Field, FiniteFloat

ExternalBarSource = Literal["sge_daily", "dukascopy", "histdata", "macro_daily"]
ExternalKLineSource = Literal["sge_daily", "dukascopy_bid", "dukascopy_ask", "histdata_bid", "macro_daily"]
XauusdOrigin = Literal["dukascopy_bid", "histdata_bid", "jd_public_kline"]
KLineSource = Literal["jd_public_kline", "jd_sim_kline", ExternalKLineSource, "xauusd_merged"]
SGE_AU9999_PRODUCT = "SGE-Au99.99"
XAUUSD_PRODUCT = "WG-XAUUSD"
MACRO_USDCNY_PRODUCT = "MACRO-USDCNY"
MACRO_DXY_PRODUCT = "MACRO-DXY"
MACRO_US10Y_PRODUCT = "MACRO-US10Y"
MACRO_DAILY_PRODUCTS: tuple[str, ...] = (MACRO_USDCNY_PRODUCT, MACRO_DXY_PRODUCT, MACRO_US10Y_PRODUCT)
XAUUSD_MERGED_SOURCE = "xauusd_merged"
XAUUSD_ORIGIN_CODES = {"dukascopy_bid": 1.0, "histdata_bid": 2.0, "jd_public_kline": 3.0}


class ExternalCandle(NamedTuple):
    """指明柱开始和源端可用时刻的外部原始行情。"""

    time: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    turnover: float | None = None


@dataclass
class ExternalBarResult:
    """单个外部行情数据序列的入库统计。"""

    written: dict[str, int] = field(default_factory=dict)
    rejected: int = 0
    error: str | None = None


class KLineBar(BaseModel):
    """单根多周期 K 线行情记录（非可成交双边报价）。"""

    product_code: str
    interval: str
    source: KLineSource
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
    available_at: AwareDatetime | None = None
    origin_source: XauusdOrigin | None = None


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
