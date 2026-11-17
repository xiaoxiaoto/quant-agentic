"""特征工程与模型预测领域模型。

定义多维度特征向量与分层模型预测输出契约。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.types.enums.trading import HoldingPeriod


class FeatureSet(BaseModel):
    """用于模型训练与实盘决策的复合特征集合。"""

    quote_id: str = Field(
        description="关联的行情报价唯一标识",
    )
    as_of: datetime = Field(
        description="特征提取基准时间戳",
    )
    return_1d: float | None = Field(
        default=None,
        description="近 1 日历史收益率",
    )
    return_3d: float | None = Field(
        default=None,
        description="近 3 日历史收益率",
    )
    return_1w: float | None = Field(
        default=None,
        description="近 1 周历史收益率",
    )
    realized_volatility: float = Field(
        default=0.0,
        description="近期历史已实现年化波动率",
    )
    atr: float = Field(
        default=0.0,
        description="真实波幅均值（ATR，元/克）",
    )
    spread: float = Field(
        default=0.0,
        description="当前银行买卖点差（元/克）",
    )
    spread_pct: float = Field(
        default=0.0,
        description="点差占中间价比例",
    )
    bank_basis: float | None = Field(
        default=None,
        description="银行相对国际参考价的基差率",
    )
    hours_to_weekend_close: float = Field(
        default=0.0,
        description="距离本周六凌晨闭市的剩余小时数",
    )
    hours_since_open: float = Field(
        default=0.0,
        description="距离本周一开盘已过去的小时数",
    )
    is_weekend_risk_window: bool = Field(
        default=False,
        description="是否处于周末闭市前的风控限制窗口期",
    )
    timesfm_forecast_return: float | None = Field(
        default=None,
        description="来自基础时序模型的零样本或校准预期收益率特征",
    )
    event_risk_flag_count: int = Field(
        default=0,
        description="来自新闻事件处理的活跃高风险标签数量",
    )


class ModelPrediction(BaseModel):
    """单个持有周期的模型预测结果。"""

    horizon: HoldingPeriod = Field(
        description="对应的目标评估持有周期",
    )
    expected_net_return: float = Field(
        description="扣除交易与退出成本后的期望净收益率",
    )
    net_return_lcb: float = Field(
        description="预期净收益率的置信区间下界（LCB）",
    )
    win_probability: float = Field(
        description="校准后的盈利概率（胜率）",
    )
    uncertainty: float = Field(
        default=0.0,
        description="模型预测方差或不确定性度量",
    )
    feature_importance: dict[str, float] = Field(
        default_factory=dict,
        description="主要输入特征贡献度字典",
    )
