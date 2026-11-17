"""账户与订单领域类型。

系统账本为影子账本，银行账单与成交记录为唯一事实权威。
对账出现任何不一致均触发 HALT_RECONCILE 状态。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.types.enums.trading import OrderState, TradeDirection


class AccountSnapshot(BaseModel):
    """影子账本账户资产快照。

    为系统内部状态视角。每笔成交后及每日必须与银行实际账单对账。
    """

    cash_available: float = 0.0
    cash_frozen: float = 0.0
    gold_total_g: float = 0.0
    gold_available_g: float = 0.0
    gold_frozen_g: float = 0.0
    cost_basis_per_g: float | None = None
    interest_pending_g: float = 0.0
    interest_credited_g: float = 0.0
    as_of: datetime | None = None


class OrderEvent(BaseModel):
    """订单生命周期事件。

    client_order_id 为全局幂等标识。
    状态为 UNKNOWN 时严禁盲目重发，必须先通过唯一订单号查单确认。
    """

    client_order_id: str
    bank_order_id: str | None = None
    order_type: str = "MARKET"
    direction: TradeDirection
    requested_grams: float
    limit_price: float | None = None
    batch_price: float | None = Field(
        default=None,
        description="非交易时段挂单的次开盘批量成交价",
    )
    filled_grams: float | None = None
    execution_price: float | None = None
    fee: float | None = None
    state: OrderState = OrderState.CREATED
    bank_event_at: datetime | None = None
    received_at: datetime | None = None
