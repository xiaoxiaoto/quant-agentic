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

    decision_id: str
    decision_at: datetime
    action: DecisionAction
    delta_g: float = Field(description="变动克数（买入为正，卖出为负）")
    target_inventory_g: float
    horizon_primary: HoldingPeriod
    forecast_ids: list[str] = Field(default_factory=list)
    expected_net_return: float | None = None
    net_return_lcb: float | None = Field(
        default=None,
        description="预期净收益率的置信区间下界（LCB）",
    )
    win_probability: float | None = None
    expected_cost: float | None = None
    uncertainty: float | None = None
    risk_flags: list[str] = Field(default_factory=list)
    quality_state: str = "NORMAL"
    reason_codes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


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
        description="周亏损风控触发线",
    )
    direction_constraint: str = "LONG_ONLY"
    leverage: float = Field(default=1.0, le=1.0, description="严禁加杠杆，仅现货多头或空仓")
