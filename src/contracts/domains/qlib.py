"""Qlib 离线量化研究、数据集、因子与实验记录领域模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.contracts.enums.trading import HoldingPeriod


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
    factor_preset: Literal["gold_core", "alpha158_lite", "custom"] = Field(description="使用的因子预设")
    instruments: list[str] = Field(default_factory=list, description="参与训练的标的列表")
    freq: str = Field(description="数据频率")
    horizons: list[HoldingPeriod] = Field(default_factory=list, description="训练与评估的目标持有期列表")
    serving_compatible: bool = Field(default=False, description="是否与线上 FeatureSet 对齐并可直接用于推理")
    oos_summary: dict[str, dict[str, float]] = Field(default_factory=dict, description="各持有期样本外滚动评估摘要")
    model_output_dir: str | None = Field(default=None, description="导出的 QlibAgent 模型产物目录")
    qlib_recorder_id: str | None = Field(default=None, description="Qlib Recorder 运行 ID（若启用）")
    created_at: datetime = Field(description="实验记录创建时间")
