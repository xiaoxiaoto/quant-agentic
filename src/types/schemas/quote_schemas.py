"""行情接口数据传输模型。

定义行情录入与查询相关的接口请求与响应载荷模型。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.types.enums.trading import DataQualityState


class QuoteIngestRequest(BaseModel):
    """行情录入请求数据模型。"""

    quote_id: str = Field(description="报价记录唯一标识")
    source_id: str = Field(description="数据源标识")
    bid_price: float = Field(description="客户卖出价（元/克）")
    ask_price: float = Field(description="客户买入价（元/克）")
    quote_at: datetime = Field(description="源头报价时间戳")
    is_tradable: bool = Field(default=True, description="是否处于可交易状态")
    quality_state: DataQualityState = Field(default=DataQualityState.NORMAL, description="数据质量状态")
    product_code: str = Field(default="GOLD_ACCUMULATION", description="产品代码")
