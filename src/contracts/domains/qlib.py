"""Qlib 离线量化研究、数据集、因子与实验记录领域模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.contracts.enums.trading import DecisionAction, HoldingPeriod

QlibFactorPreset = Literal["gold_core", "alpha158_lite", "au9999_timing", "custom"]
QlibWindowMode = Literal["expanding", "rolling"]
QlibLabelMode = Literal["net", "gross"]


class QlibFactorSpec(BaseModel):
    """单个 Qlib 表达式因子的定义与说明。"""

    name: str = Field(min_length=1, description="因子名称")
    expression: str = Field(min_length=1, description="Qlib 表达式字符串")
    description: str = Field(default="", description="因子业务含义说明")


class QlibDatasetManifest(BaseModel):
    """Qlib 数据集目录元信息清单（本地导出或外部标准目录）。"""

    source_type: Literal["local_sqlite", "external_qlib"] = Field(description="数据源类型")
    source_mode: Literal["quotes", "reference", "kline", "external"] = Field(description="数据来源细分模式")
    provider_uri: str = Field(description="Qlib provider_uri 目录路径")
    freq: str = Field(description="数据频率，例如 1h 或 day")
    instruments: list[str] = Field(default_factory=list, description="包含的标的代码列表")
    fields: list[str] = Field(default_factory=list, description="导出的字段列表")
    calendar_count: int = Field(default=0, ge=0, description="交易日历时间点总数")
    start_time: str | None = Field(default=None, description="最早数据时间标记")
    end_time: str | None = Field(default=None, description="最晚数据时间标记")
    spread_pct: float | None = Field(default=None, description="参考价合成估算点差比例")
    buy_fee_rate: float | None = Field(default=None, description="买入费率")
    sell_fee_rate: float | None = Field(default=None, description="卖出费率")
    exported_at: datetime = Field(description="清单生成或校验时间戳")


class QlibFactorMetric(BaseModel):
    """单个表达式因子的横截面/时序预测力评估指标。"""

    factor_name: str = Field(description="因子名称")
    expression: str = Field(description="因子表达式")
    sample_count: int = Field(ge=0, description="有效配对样本数")
    coverage_ratio: float = Field(ge=0.0, le=1.0, description="非空样本覆盖率")
    ic: float | None = Field(default=None, description="皮尔逊信息系数（IC）")
    rank_ic: float | None = Field(default=None, description="斯皮尔曼秩信息系数（RankIC）")
    icir: float | None = Field(default=None, description="分组信息比率（ICIR）")


class QlibExperimentRecord(BaseModel):
    """单次 Qlib 量化训练与滚动验证的实验元数据记录。"""

    experiment_name: str = Field(description="实验名称")
    run_id: str = Field(description="唯一运行标识")
    source_type: Literal["local_sqlite", "external_qlib"] = Field(description="训练数据源类型")
    provider_uri: str = Field(description="实际使用的 Qlib 数据目录")
    model_type: Literal["lgb", "linear"] = Field(description="训练模型类型")
    factor_preset: QlibFactorPreset = Field(description="使用的因子预设")
    instruments: list[str] = Field(default_factory=list, description="参与训练的标的列表")
    freq: str = Field(description="数据频率")
    horizons: list[HoldingPeriod] = Field(default_factory=list, description="训练与评估的目标持有期列表")
    serving_compatible: bool = Field(default=False, description="是否与线上 FeatureSet 对齐并可直接用于推理")
    oos_summary: dict[str, dict[str, float]] = Field(default_factory=dict, description="各持有期样本外滚动评估摘要")
    model_output_dir: str | None = Field(default=None, description="导出的 QlibAgent 模型产物目录")
    qlib_recorder_id: str | None = Field(default=None, description="Qlib Recorder 运行 ID（若启用）")
    created_at: datetime = Field(description="实验记录创建时间")


class QlibBacktestTrade(BaseModel):
    """单标的 Qlib 阈值择时回测中的单笔成交记录。"""

    timestamp: datetime = Field(description="成交时间戳")
    action: Literal["BUY", "SELL"] = Field(description="交易方向")
    price: float = Field(gt=0.0, description="成交单价")
    grams: float = Field(gt=0.0, description="成交克重")
    fee: float = Field(ge=0.0, description="本笔交易手续费")
    score: float = Field(description="触发本笔交易的模型预测得分")
    net_return_lcb: float | None = Field(default=None, description="预测净收益置信下界")
    cash_after: float = Field(ge=0.0, description="成交后可用现金")
    position_grams_after: float = Field(ge=0.0, description="成交后持仓克重")
    equity_after: float = Field(ge=0.0, description="成交后账户总权益")


class QlibBacktestEquityPoint(BaseModel):
    """单标的择时回测净值轨迹节点。"""

    timestamp: datetime = Field(description="观测时间戳")
    close_price: float = Field(gt=0.0, description="标的收盘价")
    score: float = Field(description="当日样本外预测得分")
    position_grams: float = Field(ge=0.0, description="当日收盘持仓克重")
    strategy_equity: float = Field(ge=0.0, description="策略账户权益")
    benchmark_equity: float = Field(ge=0.0, description="买入持有基准权益")


class QlibBacktestSummary(BaseModel):
    """单标的 Qlib 阈值择时回测综合绩效指标。"""

    initial_cash: float = Field(gt=0.0, description="初始资金")
    final_equity: float = Field(ge=0.0, description="期末策略权益")
    total_return: float = Field(description="策略累计收益率")
    annual_return: float = Field(description="策略年化收益率")
    sharpe_ratio: float = Field(description="年化夏普比率")
    max_drawdown: float = Field(ge=0.0, description="最大回撤比例")
    calmar_ratio: float = Field(description="卡玛比率")
    benchmark_return: float = Field(description="同期买入持有收益率")
    excess_return: float = Field(description="相对买入持有的超额收益率")
    trade_count: int = Field(ge=0, description="成交总笔数")
    round_trip_count: int = Field(ge=0, description="完整开平仓回合数")
    win_rate: float = Field(ge=0.0, le=1.0, description="完整回合胜率")
    total_fees: float = Field(ge=0.0, description="累计手续费支出")
    bars_evaluated: int = Field(ge=0, description="参与回测的有效样本根数")


class QlibSignalRecord(BaseModel):
    """Qlib 单标的盘前择时信号与特征快照。"""

    signal_id: str = Field(description="信号唯一标识")
    instrument: str = Field(description="目标品种代码")
    as_of: datetime = Field(description="信号计算所依据的最新数据时间戳")
    horizon: HoldingPeriod = Field(description="预测持有期")
    predicted_return: float = Field(description="预测预期收益率（score）")
    net_return_lcb: float = Field(description="预测净收益置信下界")
    win_probability: float = Field(ge=0.0, le=1.0, description="校准后盈利概率")
    action: DecisionAction = Field(description="按阈值规则生成的择时动作建议")
    buy_thresh: float = Field(description="买入阈值")
    sell_thresh: float = Field(description="卖出阈值")
    latest_close: float = Field(gt=0.0, description="最新收盘价")
    model_dir: str = Field(description="使用的 QlibAgent 模型目录")
    alert_id: str | None = Field(default=None, description="若推送至人工复核队列则返回提醒 ID")
    feature_snapshot: dict[str, float | None] = Field(default_factory=dict, description="本次推理使用的特征快照")
    created_at: datetime = Field(description="信号生成时间")
