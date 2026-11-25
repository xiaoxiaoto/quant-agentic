"""成交模型与执行适配领域模型。

定义 RFQ（报价接受）成交请求、执行结果及非交易时段挂单契约。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.contracts.domains.account import AccountSnapshot, OrderEvent
from src.contracts.domains.alert import AlertPayload
from src.contracts.domains.decision import Decision
from src.contracts.domains.feature import ModelPrediction
from src.contracts.domains.reference_quote import ReferenceQuote
from src.contracts.enums.trading import RiskState, TradeDirection


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


class PaperStepResult(BaseModel):
    """单步本地模拟交易评估与执行结果。"""

    as_of: datetime = Field(
        description="本次评估基准时间戳",
    )
    product_code: str = Field(
        description="交易标的产品编码",
    )
    quote_id: str | None = Field(
        default=None,
        description="本次使用的行情标识",
    )
    prediction: ModelPrediction | None = Field(
        default=None,
        description="本次模型预测输出",
    )
    raw_decision: Decision | None = Field(
        default=None,
        description="元决策引擎原始建议",
    )
    approved_decision: Decision | None = Field(
        default=None,
        description="独立风控核验后的最终决策",
    )
    risk_violations: list[str] = Field(
        default_factory=list,
        description="触发的风控违规或阻断项列表",
    )
    order: OrderEvent | None = Field(
        default=None,
        description="已提交或已成交的模拟订单事件",
    )
    fill: RFQFillResult | None = Field(
        default=None,
        description="RFQ 成交仿真回报结果",
    )
    snapshot: AccountSnapshot = Field(
        description="本次步骤完成后的影子账本快照",
    )
    message: str = Field(
        default="",
        description="本次执行摘要说明",
    )


class LiveStepResult(BaseModel):
    """单步实盘辅助评估与合规提醒结果。"""

    as_of: datetime = Field(
        description="本次评估基准时间戳",
    )
    product_code: str = Field(
        description="评估标的产品编码",
    )
    quote_id: str | None = Field(
        default=None,
        description="本次使用的可成交报价标识",
    )
    risk_state: RiskState = Field(
        default=RiskState.NORMAL,
        description="当前实盘风控状态",
    )
    kill_switch_active: bool = Field(
        default=False,
        description="人工急停开关是否处于激活状态",
    )
    prediction: ModelPrediction | None = Field(
        default=None,
        description="本次模型预测输出",
    )
    raw_decision: Decision | None = Field(
        default=None,
        description="元决策引擎原始建议",
    )
    approved_decision: Decision | None = Field(
        default=None,
        description="独立风控核验后的最终决策",
    )
    risk_violations: list[str] = Field(
        default_factory=list,
        description="触发的风控违规或阻断项列表",
    )
    alert: AlertPayload | None = Field(
        default=None,
        description="生成的对外合规交易提醒（观望或弃权时为空）",
    )
    reference_quote: ReferenceQuote | None = Field(
        default=None,
        description="可选最新参考行情展示信息（不参与交易决策）",
    )
    is_reference_estimated: bool = Field(
        default=False,
        description="本次评估是否基于参考价按估算点差生成（仅限人工复核提醒，严禁自动下单）",
    )
    snapshot: AccountSnapshot = Field(
        description="当前本地影子账本快照",
    )
    message: str = Field(
        default="",
        description="本次实盘评估摘要说明",
    )
