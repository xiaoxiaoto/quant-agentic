"""基准策略与评估指标领域模型。

用于基准策略评测、消融对比及影子运行绩效归因分析。
"""

from datetime import datetime

from pydantic import BaseModel, Field


class BaselineMetrics(BaseModel):
    """基准策略评估指标。

    衡量买入持有、定投及现金策略在历史回测中的绩效表现。
    """

    strategy_name: str = Field(
        description="基准策略名称（如 BUY_AND_HOLD, DCA, CASH_ONLY）",
    )
    total_return_pct: float = Field(
        description="回测区间累计总收益率",
    )
    annualized_return_pct: float = Field(
        description="换算年化收益率",
    )
    max_drawdown_pct: float = Field(
        description="回测区间最大动态回撤幅度",
    )
    sharpe_ratio: float | None = Field(
        default=None,
        description="年化夏普比率",
    )
    total_trades: int = Field(
        default=0,
        description="回测期间发生的交易总笔数",
    )
    total_fee_cny: float = Field(
        default=0.0,
        description="回测期间累计扣除的交易手续费总额（元）",
    )
    final_equity: float = Field(
        default=0.0,
        description="回测期末账户总权益金额（元）",
    )


class BaselineComparisonReport(BaseModel):
    """多基准横向对比报告。"""

    report_id: str = Field(
        description="对比报告唯一标识",
    )
    as_of: datetime = Field(
        description="报告评估生成时间戳",
    )
    metrics: list[BaselineMetrics] = Field(
        default_factory=list,
        description="各基准策略绩效指标列表",
    )
    best_strategy: str = Field(
        default="",
        description="横向评测表现最优的策略名称",
    )
    summary: str = Field(
        default="",
        description="基准评测综合结论描述",
    )


class ShadowRunReport(BaseModel):
    """影子运行与回测验证综合绩效报告。"""

    run_id: str = Field(
        description="影子运行唯一标识",
    )
    start_time: datetime = Field(
        description="运行评估区间起始时间戳",
    )
    end_time: datetime = Field(
        description="运行评估区间截止时间戳",
    )
    initial_cash: float = Field(
        description="初始本金规模（元）",
    )
    final_equity: float = Field(
        description="期末账户总资产净值（元）",
    )
    total_return_pct: float = Field(
        description="区间扣费累计净收益率",
    )
    annualized_return_pct: float = Field(
        description="区间换算年化收益率",
    )
    max_drawdown_pct: float = Field(
        description="历史最大动态净值回撤",
    )
    sharpe_ratio: float | None = Field(
        default=None,
        description="未调整标准夏普比率",
    )
    deflated_sharpe: float | None = Field(
        default=None,
        description="多重检验修正指标；当前未实现，返回空值",
    )
    total_trades: int = Field(
        default=0,
        description="累计成交交易笔数",
    )
    winning_trades: int = Field(
        default=0,
        description="盈利交易笔数",
    )
    win_rate: float = Field(
        default=0.0,
        description="平仓交易胜率比例（盈利卖出笔数 / 已完成卖出笔数）",
    )
    total_fees_cny: float = Field(
        default=0.0,
        description="累计发生的实际手续费总额（元），滑点已计入成交价格",
    )
    summary: str = Field(
        default="",
        description="影子运行绩效评价总结",
    )
