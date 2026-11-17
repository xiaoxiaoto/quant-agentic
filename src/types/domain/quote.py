"""行情报价领域类型。

字段命名采用客户视角：
    bid_price —— 客户卖出价（银行买入价）
    ask_price —— 客户买入价（银行卖出价）
与各数据源对接时必须首先核验买卖价格方向定义。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.types.enums.trading import DataQualityState


class QuoteRaw(BaseModel):
    """银行/平台原始双边报价记录。

    写入后不可篡改。包含原始载荷哈希用于审计追踪。
    """

    quote_id: str
    source_id: str
    bid_price: float = Field(description="客户卖出价（银行买入价）")
    ask_price: float = Field(description="客户买入价（银行卖出价）")
    quote_at: datetime = Field(description="数据源提供的时间戳（含时区）")
    received_at: datetime = Field(description="本地系统接收时间戳")
    available_at: datetime | None = Field(
        default=None,
        description="通过数据质量门禁后可用于策略决策的时间戳（PIT 时间链）",
    )
    is_tradable: bool = Field(
        description="当前是否处于可交易状态（休市、暂停交易等为 False）",
    )
    response_code: str | None = None
    payload_hash: str | None = None
    quality_state: DataQualityState = DataQualityState.NORMAL
    product_code: str = "GOLD_ACCUMULATION"


class QuoteDerived(BaseModel):
    """衍生报价指标，用于特征工程。

    bank_basis 为银行产品特有基差特征：
        bank_basis = bank_mid / intl_ref_cny_per_g − 1
    必须以真实银行历史报价校验，严禁以 SGE 或外盘金价替代银行成交价。
    """

    quote_id: str
    spread: float = Field(description="买卖点差：ask_price − bid_price（元/克）")
    spread_pct: float = Field(description="点差率：spread / mid_price")
    mid_price: float = Field(description="中间价：(bid + ask) / 2")
    intl_ref_cny_per_g: float | None = Field(
        default=None,
        description="国际黄金折合人民币价格：XAUUSD × USDCNH / 31.1034768",
    )
    bank_basis: float | None = Field(
        default=None,
        description="银行相对国际参考价的基差率：bank_mid / intl_ref − 1",
    )
