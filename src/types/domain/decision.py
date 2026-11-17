"""决策引擎与风控领域类型。

风控与决策优先级：
    产品规则与授权 > 人工急停 > 账户与系统风险 > 事件风险 > 策略模型信号。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.types.enums.trading import DecisionAction, HoldingPeriod


class Decision(BaseModel):
    """决策引擎输出契约。

    相同 decision_id 必须具备幂等性。
    全部输入版本、模型预测值与判定原因码均需持久化以备审计。
    """

    decision_id: str = Field(
        description="决策指令唯一幂等标识",
    )
    decision_at: datetime = Field(
        description="生成决策的时间戳",
    )
    action: DecisionAction = Field(
        description="决策动作（买入、持有、卖出、弃权）",
    )
    delta_g: float = Field(
        description="变动克数（买入为正，卖出为负）",
    )
    target_inventory_g: float = Field(
        description="调仓后的目标黄金总持仓克数",
    )
    horizon_primary: HoldingPeriod = Field(
        description="本笔决策预期评估的主持有周期",
    )
    forecast_ids: list[str] = Field(
        default_factory=list,
        description="关联的模型预测输入标识列表",
    )
    expected_net_return: float | None = Field(
        default=None,
        description="模型预测扣费净收益率期望值",
    )
    net_return_lcb: float | None = Field(
        default=None,
        description="预期净收益率的置信区间下界（LCB）",
    )
    win_probability: float | None = Field(
        default=None,
        description="模型预估交易胜率概率",
    )
    expected_cost: float | None = Field(
        default=None,
        description="预估单笔往返交易总成本率",
    )
    uncertainty: float | None = Field(
        default=None,
        description="模型不确定性或方差度量",
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description="触发的风控警示标签列表",
    )
    quality_state: str = Field(
        default="NORMAL",
        description="决策输入时点的行情质量状态",
    )
    reason_codes: list[str] = Field(
        default_factory=list,
        description="决策生成的结构化原因码列表",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="决策建议有效截止时间戳",
    )


class RiskBudget(BaseModel):
    """风控限额模型。

    所有阈值均为候选值，需根据实际账户资金规模校准后冻结。
    不构成任何投资建议。
    """

    single_trade_net_exit_loss_pct: float = Field(
        default=0.0025,
        description="单笔净退出损失预算，占权益比例（候选：0.25%）",
    )
    max_gold_exposure_pct: float | None = Field(
        default=None,
        description="黄金最大总敞口，占账户权益比例",
    )
    weekend_risk_bucket_limit: float | None = Field(
        default=None,
        description="周末跳空风险桶上限（回测后确定）",
    )
    daily_loss_trigger_pct: float = Field(
        default=0.01,
        description="日亏损风控触发线，占权益比例（候选：1%）",
    )
    weekly_loss_trigger_pct: float | None = Field(
        default=None,
        description="周亏损风控触发线，占权益比例",
    )
    direction_constraint: str = Field(
        default="LONG_ONLY",
        description="持仓方向约束（仅现货多头或空仓）",
    )
    leverage: float = Field(
        default=1.0,
        le=1.0,
        description="严禁加杠杆，仅现货多头或空仓",
    )
