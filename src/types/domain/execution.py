"""成交模型与执行适配领域模型。

定义 RFQ（报价接受）成交请求、执行结果及非交易时段挂单契约。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.types.enums.trading import TradeDirection


class RFQRequest(BaseModel):
    """RFQ（报价接受）执行请求载荷。"""

    request_id: str = Field(
        description="请求全局唯一幂等标识",
    )
    direction: TradeDirection = Field(
        description="交易方向（买入或卖出）",
    )
    grams: float = Field(
        description="请求交易克重（克）",
    )
    decision_time: datetime = Field(
        description="产生决策的基准时间戳",
    )
    eligible_at: datetime = Field(
        description="经过系统处理延迟后最早可开始成交的时间戳",
    )
    is_programmatic: bool = Field(
        default=False,
        description="是否为程序化自动执行（False 为人工确认路径）",
    )


class RFQFillResult(BaseModel):
    """RFQ（报价接受）模拟或真实成交回报结果。"""

    fill_id: str = Field(
        description="成交记录唯一标识",
    )
    request_id: str = Field(
        description="关联的执行请求标识",
    )
    is_filled: bool = Field(
        description="是否成功成交（若因超时、拒单或滑点超限则为 False）",
    )
    filled_price: float | None = Field(
        default=None,
        description="最终实际成交价格（元/克）",
    )
    filled_grams: float = Field(
        default=0.0,
        description="实际成交克数（克）",
    )
    fee_cny: float = Field(
        default=0.0,
        description="本次交易发生的手续费金额（元）",
    )
    slippage_cny: float = Field(
        default=0.0,
        description="相对可得报价的实际滑点偏差（元/克）",
    )
    filled_at: datetime | None = Field(
        default=None,
        description="实际撮合成交时间戳",
    )
    rejection_reason: str | None = Field(
        default=None,
        description="未成交或被拒单的具体原因说明",
    )


class BatchOrderInstruction(BaseModel):
    """非交易时段挂单指令（次开盘批量成交价机制）。"""

    instruction_id: str = Field(
        description="挂单指令唯一幂等标识",
    )
    direction: TradeDirection = Field(
        description="挂单交易方向",
    )
    requested_grams: float = Field(
        description="期望委托克数（克）",
    )
    limit_price: float = Field(
        description="期望委托限价（买单上限或卖单下限，元/克）",
    )
    placed_at: datetime = Field(
        description="提交挂单的时间戳（非交易时段内）",
    )
    target_session_open: datetime = Field(
        description="次开盘生效时间戳",
    )
    batch_price: float | None = Field(
        default=None,
        description="次开盘银行公布的批量撮合价格（元/克）",
    )
    is_executed: bool = Field(
        default=False,
        description="次开盘批量比价后是否满足条件撮合成交",
    )
