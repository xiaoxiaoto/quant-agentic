"""账户与订单领域类型。

系统账本为影子账本，银行账单与成交记录为唯一事实权威。
对账出现任何不一致均触发 HALT_RECONCILE 状态。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.contracts.enums.trading import OrderState, TradeDirection


class AccountSnapshot(BaseModel):
    """影子账本账户资产快照。

    为系统内部状态视角。每笔成交后及每日必须与银行实际账单对账。
    """

    cash_available: float = Field(
        default=0.0,
        description="当前账户可用现金余额（元）",
    )
    cash_frozen: float = Field(
        default=0.0,
        description="挂单或交易处理中冻结的现金金额（元）",
    )
    gold_total_g: float = Field(
        default=0.0,
        description="持仓黄金总克重（克）",
    )
    gold_available_g: float = Field(
        default=0.0,
        description="当前可用可卖出或转换的黄金克重（克）",
    )
    gold_frozen_g: float = Field(
        default=0.0,
        description="委托挂单中冻结的黄金克重（克）",
    )
    cost_basis_per_g: float | None = Field(
        default=None,
        description="持仓加权平均成本单价（元/克）",
    )
    realized_pnl: float = Field(
        default=0.0,
        description="历史累计已实现盈亏（元）",
    )
    unrealized_pnl: float = Field(
        default=0.0,
        description="基于当前市场价计算的浮动盈亏（元）",
    )
    interest_pending_g: float = Field(
        default=0.0,
        description="尚未达到发放门槛的待结计息克数（克）",
    )
    interest_credited_g: float = Field(
        default=0.0,
        description="已累计发放到账的利息黄金克重（克）",
    )
    as_of: datetime | None = Field(
        default=None,
        description="资产快照生成时间戳",
    )


class OrderEvent(BaseModel):
    """订单生命周期事件。

    client_order_id 为全局幂等标识。
    状态为 UNKNOWN 时严禁盲目重发，必须先通过唯一订单号查单确认。
    """

    client_order_id: str = Field(
        description="本地全局唯一的幂等客户订单标识",
    )
    bank_order_id: str | None = Field(
        default=None,
        description="银行端返回的官方订单编号",
    )
    order_type: str = Field(
        default="MARKET",
        description="订单委托类型（市价或限价）",
    )
    direction: TradeDirection = Field(
        description="交易方向（买入或卖出）",
    )
    requested_grams: float = Field(
        description="委托申报的黄金克重（克）",
    )
    limit_price: float | None = Field(
        default=None,
        description="限价单设定的委托价格（元/克）",
    )
    batch_price: float | None = Field(
        default=None,
        description="非交易时段挂单的次开盘批量成交价",
    )
    filled_grams: float | None = Field(
        default=None,
        description="实际撮合成交的黄金克重（克）",
    )
    execution_price: float | None = Field(
        default=None,
        description="实际成交的单价（元/克）",
    )
    fee: float | None = Field(
        default=None,
        description="本次成交扣除的手续费金额（元）",
    )
    state: OrderState = Field(
        default=OrderState.CREATED,
        description="订单生命周期当前状态",
    )
    bank_event_at: datetime | None = Field(
        default=None,
        description="银行端记录的事件发生时间戳",
    )
    received_at: datetime | None = Field(
        default=None,
        description="本地接收到订单回报的时间戳",
    )


class BankStatementItem(BaseModel):
    """银行官方交易对账流水记录。

    以银行为最终权威账目，用于核验影子账本。
    """

    bank_order_id: str = Field(
        description="银行官方交易流水唯一订单号",
    )
    client_order_id: str | None = Field(
        default=None,
        description="对应的本地幂等订单标识",
    )
    direction: TradeDirection = Field(
        description="银行记录的交易方向",
    )
    grams: float = Field(
        description="银行记录的成交克重（克）",
    )
    price: float = Field(
        description="银行记录的成交单价（元/克）",
    )
    fee: float = Field(
        default=0.0,
        description="银行记录的扣除手续费（元）",
    )
    executed_at: datetime = Field(
        description="银行实际成交时间戳",
    )
    status: str = Field(
        default="SUCCESS",
        description="银行交易流水状态",
    )


class ReconciliationDiscrepancy(BaseModel):
    """单项对账差异明细。"""

    field_name: str = Field(
        description="核验差异的字段名称",
    )
    local_value: float | str = Field(
        description="本地系统记录的账面数值",
    )
    bank_value: float | str = Field(
        description="银行官方记录的权威数值",
    )
    difference: float = Field(
        description="本地与银行数值的绝对差异量",
    )
    tolerance: float = Field(
        description="该指标允许的最大容差阈值",
    )
    is_breached: bool = Field(
        description="差异是否超出预设容差阈值",
    )


class ReconciliationReport(BaseModel):
    """对账检查报告。

    对比本地影子账本与银行账目，输出一致性核验结果。
    """

    report_id: str = Field(
        description="对账报告唯一审计标识",
    )
    as_of: datetime = Field(
        description="对账基准截止时间戳",
    )
    matched: bool = Field(
        description="本地账本与银行账目是否完全一致无违规差异",
    )
    discrepancies: list[ReconciliationDiscrepancy] = Field(
        default_factory=list,
        description="发现的超出容差差异项明细",
    )
    unmatched_local_orders: list[str] = Field(
        default_factory=list,
        description="本地已记录但银行未匹配的订单号列表",
    )
    unmatched_bank_orders: list[str] = Field(
        default_factory=list,
        description="银行有流水但本地未记录的订单号列表",
    )
    summary: str = Field(
        default="",
        description="对账核验结论摘要描述",
    )
