"""回测、模拟交易、实盘辅助与提醒执行接口数据传输模型。"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, NonNegativeFloat, PositiveFloat, PositiveInt, model_validator

from src.contracts.domains.account import OrderEvent
from src.contracts.domains.execution import BatchOrderInstruction, RFQFillResult
from src.contracts.enums.trading import PaperEngine, TradeDirection


class BacktestRunRequest(BaseModel):
    """历史 PIT 回测运行请求模型。"""

    product_code: str | None = Field(default=None, min_length=1, description="回测产品编码，缺省读取默认配置")
    start_time: AwareDatetime | None = Field(default=None, description="回测起始时间（带时区）")
    end_time: AwareDatetime | None = Field(default=None, description="回测结束时间（带时区）")
    quotes_file: str | None = Field(default=None, description="可选离线行情 JSONL 文件路径")
    initial_cash: PositiveFloat | None = Field(default=None, allow_inf_nan=False, description="初始资金（元）")
    trade_grams: PositiveFloat | None = Field(default=None, allow_inf_nan=False, description="单次决策基准克重（克）")
    dca_step_interval: PositiveInt = Field(default=5, description="定投基准采样步长")


class FeasibilityRequest(BaseModel):
    """经济可行性测算请求模型。"""

    annual_volatility: PositiveFloat = Field(default=0.17, allow_inf_nan=False, description="年化波动率假设")
    spread_scenarios: list[NonNegativeFloat] = Field(
        default_factory=lambda: [0.0005, 0.0010, 0.0020, 0.0030],
        min_length=1,
        description="待测算点差情景列表",
    )


class FeasibilityRowResponse(BaseModel):
    """单一持有期与点差组合的测算行响应模型。"""

    holding_period: str = Field(description="持有周期标签")
    trading_hours: float = Field(description="周期对应有效交易小时数")
    sigma_h: float = Field(description="周期波动率")
    typical_move_m: float = Field(description="典型绝对价格变动幅度")
    spread_pct: float = Field(description="双边点差比例")
    total_cost_pct: float = Field(description="往返总交易成本比例")
    required_accuracy: float = Field(description="保本所需方向胜率")
    assessment: str = Field(description="可行性评级")


class FeasibilityReportResponse(BaseModel):
    """经济可行性测算报告响应模型。"""

    annual_volatility: float = Field(description="年化波动率假设")
    trading_hours_per_year: float = Field(description="年有效交易小时数")
    buy_fee_rate: float = Field(description="买入手续费率")
    sell_fee_rate: float = Field(description="卖出手续费率")
    spread_scenarios: list[float] = Field(description="点差情景列表")
    has_viable_period: bool = Field(description="是否存在可行持有期")
    rows: list[FeasibilityRowResponse] = Field(default_factory=list, description="逐行测算明细")


class PaperStepRequest(BaseModel):
    """单步本地模拟交易评估请求模型。"""

    product_code: str | None = Field(default=None, min_length=1, description="产品编码，缺省读取默认配置")
    trade_grams: PositiveFloat | None = Field(default=None, allow_inf_nan=False, description="目标交易克重（克）")


class PaperOrderRequest(BaseModel):
    """手动提交模拟交易委托请求模型。"""

    engine: PaperEngine = Field(default=PaperEngine.LOCAL, description="模拟盘引擎（local 或 jd）")
    direction: TradeDirection = Field(description="交易方向（BUY 或 SELL）")
    product_code: str | None = Field(default=None, min_length=1, description="本地模拟盘产品编码")
    bank: str = Field(default="CZB", min_length=1, description="京东模拟盘银行代码")
    grams: PositiveFloat | None = Field(default=None, allow_inf_nan=False, description="委托克重（克）")
    amount: PositiveFloat | None = Field(default=None, allow_inf_nan=False, description="买入金额（仅 jd 引擎）")
    ratio: PositiveFloat | None = Field(default=None, allow_inf_nan=False, description="卖出比例（仅 jd 引擎）")
    confirm: bool = Field(default=False, description="向京东模拟盘下单时的显式确认开关")


class LocalPaperOrderResponse(BaseModel):
    """本地影子模拟盘手动下单响应模型。"""

    order: OrderEvent = Field(description="订单事件记录")
    fill: RFQFillResult = Field(description="RFQ 模拟撮合结果")


class LiveStepRequest(BaseModel):
    """单次实盘信号评估请求模型。"""

    product_code: str | None = Field(default=None, min_length=1, description="可成交报价产品编码")
    trade_grams: PositiveFloat | None = Field(default=None, allow_inf_nan=False, description="目标建议克重（克）")
    reference_product_code: str = Field(default="CMBC-JCJ", min_length=1, description="仅作展示的参考行情编码")


class KillSwitchRequest(BaseModel):
    """触发人工急停开关请求模型。"""

    reason: str = Field(default="MANUAL_EMERGENCY_HALT", min_length=1, description="急停原因说明")


class KillSwitchStatusResponse(BaseModel):
    """风控急停状态响应模型。"""

    kill_switch_active: bool = Field(description="急停开关当前是否激活")
    message: str = Field(description="操作结果说明")


class BatchOrderRegisterRequest(BaseModel):
    """登记非交易时段次开盘批量挂单请求模型。"""

    instruction_id: str = Field(min_length=1, description="挂单指令唯一 ID")
    direction: TradeDirection = Field(description="交易方向（BUY 或 SELL）")
    grams: PositiveFloat = Field(allow_inf_nan=False, description="委托克重（克）")
    limit_price: PositiveFloat = Field(allow_inf_nan=False, description="委托限价（元/克）")
    target_open: AwareDatetime = Field(description="次开盘生效时间（带时区）")


class BatchOrderExecuteRequest(BaseModel):
    """次开盘银行批量价核算挂单请求模型。"""

    bank_batch_price: PositiveFloat = Field(allow_inf_nan=False, description="银行公布批量成交价（元/克）")


class BatchOrderExecuteResponse(BaseModel):
    """次开盘批量挂单核算结果响应模型。"""

    filled: bool = Field(description="是否满足限价条件并成交")
    instruction: BatchOrderInstruction = Field(description="更新后的挂单指令明细")


class AlertExecuteRequest(BaseModel):
    """标记交易提醒已实际成交时的可选回填载荷。"""

    filled_grams: PositiveFloat | None = Field(default=None, allow_inf_nan=False, description="实际成交克重（克）")
    filled_price: PositiveFloat | None = Field(default=None, allow_inf_nan=False, description="实际成交单价（元/克）")
    fee: NonNegativeFloat | None = Field(default=None, allow_inf_nan=False, description="实际手续费（元）")
    bank_order_id: str | None = Field(default=None, description="银行官方订单号")

    @model_validator(mode="after")
    def _validate_fill_pair(self) -> "AlertExecuteRequest":
        """成交克重与单价必须成对提供或同时省略，防止部分填写静默跳过记账。"""
        if (self.filled_grams is None) != (self.filled_price is None):
            raise ValueError("filled_grams 与 filled_price 必须同时提供或同时留空")
        return self


class FifoLotItem(BaseModel):
    """先进先出在持批次明细记录。"""

    client_order_id: str = Field(description="委托单唯一标识")
    bank_order_id: str | None = Field(default=None, description="银行官方订单流水号")
    direction: TradeDirection = Field(description="交易方向")
    lot_status: Literal["HOLDING", "PARTIAL", "CLOSED", "SELL", "UNFILLED"] = Field(
        description="在持状态：HOLDING(在持)/PARTIAL(部分卖出)/CLOSED(已平仓)/SELL(卖出平仓)/UNFILLED(未成交)"
    )
    requested_grams: float = Field(description="原始委托克重（克）")
    filled_grams: float | None = Field(default=None, description="实际成交克重（克）")
    remaining_grams: float = Field(default=0.0, description="当前在持剩余克重（克）")
    execution_price: float | None = Field(default=None, description="成交价格（元/克）")
    fee: float | None = Field(default=None, description="手续费（元）")
    lot_pnl: float | None = Field(default=None, description="按参考价重估浮动盈亏（元）")
    timestamp: str | None = Field(default=None, description="成交时间字符串（ISO 或本地格式）")


class FifoSummaryResponse(BaseModel):
    """影子账本先进先出匹配汇总与切片明细。"""

    open_position_cost: float = Field(description="当前在持批次总成本（元）")
    open_grams: float = Field(description="当前在持总克重（克）")
    open_lot_count: int = Field(description="当前在持批次数")
    total_orders: int = Field(description="总历史订单记录数")
    items: list[FifoLotItem] = Field(description="切片订单明细列表")
