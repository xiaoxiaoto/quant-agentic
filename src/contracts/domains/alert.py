"""交易提醒与人工确认领域模型。

定义向用户推送的交易决策建议载荷与人工确认全状态生命周期。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.contracts.enums.trading import AlertState, DecisionAction, HoldingPeriod


class AlertPayload(BaseModel):
    """提醒卡片与消息推送数据载荷。

    内容遵循合规披露要求：
    包含方向、克重、当前价格与点差、盈亏平衡价、净退出损失、持有期与失效时间、
    原因码、风控标签及合规免责声明。
    """

    alert_id: str = Field(
        description="提醒条目全局唯一审计标识",
    )
    decision_id: str = Field(
        description="关联的元决策指令唯一标识",
    )
    created_at: datetime = Field(
        description="提醒生成并初创的时间戳",
    )
    expires_at: datetime = Field(
        description="提醒有效截止时间戳（超时自动作废）",
    )
    action: DecisionAction = Field(
        description="建议交易动作（买入或卖出）",
    )
    suggested_grams: float = Field(
        description="建议交易黄金克重（克）",
    )
    current_bid_price: float = Field(
        description="当前客户卖出价（银行买入价，元/克）",
    )
    current_ask_price: float = Field(
        description="当前客户买入价（银行卖出价，元/克）",
    )
    spread: float = Field(
        description="当前买卖点差（元/克）",
    )
    break_even_price: float = Field(
        description="预期需达到的保本盈亏平衡价格（元/克）",
    )
    net_exit_loss_cny: float = Field(
        description="每克综合净退出损失预估金额（元/克）",
    )
    horizon_primary: HoldingPeriod = Field(
        description="建议评估的主持有周期",
    )
    reason_codes: list[str] = Field(
        default_factory=list,
        description="模型决策与风控给出的理由代码列表",
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description="伴随的风控警示标签列表",
    )
    state: AlertState = Field(
        default=AlertState.INTENT_CREATED,
        description="提醒流转当前状态",
    )
    disclaimer: str = Field(
        default="该信号为研究输出，非投资建议",
        description="合规性风险提示免责声明",
    )
    user_confirmed_at: datetime | None = Field(
        default=None,
        description="用户确认交易意图时间戳",
    )
    user_executed_at: datetime | None = Field(
        default=None,
        description="用户在银行端完成下单时间戳",
    )
