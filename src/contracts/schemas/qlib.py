"""Qlib 离线量化研究、数据导出、因子评估与训练接口契约。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.contracts.domains.market_bar import KLineSource
from src.contracts.domains.qlib import (
    QlibBacktestEquityPoint,
    QlibBacktestSummary,
    QlibBacktestTrade,
    QlibDatasetManifest,
    QlibExperimentRecord,
    QlibFactorMetric,
    QlibFactorPreset,
    QlibLabelMode,
    QlibSignalRecord,
    QlibWindowMode,
)
from src.contracts.enums.trading import HoldingPeriod
from src.contracts.schemas.model import WalkForwardFoldReport

QlibExportFrequency = Literal["1min", "5min", "15min", "30min", "1h", "day"]


class QlibStatusResponse(BaseModel):
    """Qlib 可选运行时状态、数据源与实验总览。"""

    pyqlib_installed: bool = Field(description="当前 Python 环境是否已安装 pyqlib")
    pyqlib_version: str | None = Field(default=None, description="已安装的 pyqlib 版本号")
    qlib_enabled: bool = Field(description="配置中是否启用 Qlib 研究能力")
    predictor_backend: str = Field(description="当前配置的预测后端模式")
    provider_uri: str = Field(description="本地 SQLite 导出 Qlib 数据根目录")
    external_provider_uri: str | None = Field(default=None, description="可选外部标准 Qlib 数据源路径")
    active_model_dir: str | None = Field(default=None, description="当前配置的 Qlib 线上模型目录")
    local_dataset: QlibDatasetManifest | None = Field(default=None, description="本地已导出数据集清单")
    external_dataset: QlibDatasetManifest | None = Field(default=None, description="外部标准数据集清单")
    experiment_count: int = Field(default=0, ge=0, description="本地记录的 Qlib 实验总数")


class QlibExportRequest(BaseModel):
    """从本地 SQLite 导出 Qlib 二进制数据集的请求载荷。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_mode: Literal["quotes", "reference", "kline"] = Field(
        default="reference",
        description="本地行情源：quotes（双边报价）/ reference（参考行情）/ kline（多周期 K 线）",
    )
    instruments: list[str] | None = Field(
        default=None,
        description="待导出品种编码列表；为空时按模式取默认标的",
    )
    freq: QlibExportFrequency = Field(default="1h", description="导出频率，分钟频仅用于 XAUUSD 合并行情")
    kline_interval: str = Field(default="m60", min_length=1, description="kline 模式下的源 K 线周期（如 m60/day）")
    kline_source: KLineSource | None = Field(default=None, description="kline 模式下按采集来源筛选")
    spread_pct: float = Field(default=0.002, gt=0.0, lt=0.05, allow_inf_nan=False, description="估算双边点差比例")
    exclude_synthetic: bool = Field(default=True, description="quotes 模式下是否排除模拟合成报价")
    include_macro_features: bool = Field(
        default=False,
        description="日频 kline 导出时是否按 PIT 拼入 factor、xau_ret、usdcny_ret、dxy_ret、us10y_diff",
    )
    output_dir: str | None = Field(default=None, min_length=1, description="可选导出目录（默认使用 QLIB_PROVIDER_URI）")


class QlibExportReport(BaseModel):
    """本地 SQLite 导出 Qlib 数据集的执行报告。"""

    manifest: QlibDatasetManifest = Field(description="导出生成的数据集元信息清单")
    written_files: int = Field(ge=0, description="生成的二进制与元数据文件总数")
    skipped_instruments: list[str] = Field(default_factory=list, description="因无有效数据而跳过的品种列表")


class QlibFactorEvaluateRequest(BaseModel):
    """基于 Qlib 表达式引擎的因子预测力评估请求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_type: Literal["local_sqlite", "external_qlib"] = Field(
        default="local_sqlite",
        description="数据源：local_sqlite（本地导出数据）或 external_qlib（外部标准 Qlib 数据）",
    )
    provider_uri: str | None = Field(default=None, min_length=1, description="可选覆盖数据目录路径")
    instruments: list[str] | None = Field(default=None, description="待评估标的列表，为空时使用全部标的")
    freq: Literal["1h", "day"] = Field(default="1h", description="评估频率")
    horizon: HoldingPeriod = Field(default=HoldingPeriod.D1, description="扣费净收益标签持有期")
    factor_preset: QlibFactorPreset = Field(
        default="gold_core",
        description="因子预设组：gold_core / alpha158_lite / au9999_timing / custom",
    )
    label_mode: QlibLabelMode = Field(default="net", description="标签模式：net（扣费净收益）或 gross（原始收益率）")
    custom_expressions: dict[str, str] = Field(
        default_factory=dict,
        description="自定义 Qlib 因子表达式映射（因子名 -> 表达式）",
    )
    start_time: str | None = Field(default=None, description="可选评估起始时间字符串")
    end_time: str | None = Field(default=None, description="可选评估结束时间字符串")


class QlibFactorEvaluateReport(BaseModel):
    """Qlib 表达式因子 IC / RankIC 评估报告。"""

    source_type: Literal["local_sqlite", "external_qlib"] = Field(description="数据源类型")
    provider_uri: str = Field(description="实际数据目录")
    freq: str = Field(description="评估频率")
    horizon: HoldingPeriod = Field(description="目标持有期")
    factor_preset: str = Field(description="使用的因子预设")
    instruments: list[str] = Field(default_factory=list, description="实际参与评估的标的列表")
    metrics: list[QlibFactorMetric] = Field(default_factory=list, description="各因子评估指标列表")


class QlibTrainRequest(BaseModel):
    """Qlib 量化模型训练、滚动验证与产物导出请求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    experiment_name: str = Field(default="gold_qlib_research", min_length=1, max_length=120, description="实验名称")
    source_type: Literal["local_sqlite", "external_qlib"] = Field(
        default="local_sqlite",
        description="训练数据源类型",
    )
    provider_uri: str | None = Field(default=None, min_length=1, description="可选覆盖数据目录路径")
    instruments: list[str] | None = Field(default=None, description="参与训练的标的列表")
    freq: Literal["1h", "day"] = Field(default="1h", description="训练数据频率")
    model_type: Literal["lgb", "linear"] = Field(default="lgb", description="Qlib 模型类型：lgb 或 linear")
    factor_preset: QlibFactorPreset = Field(
        default="gold_core",
        description="因子预设（gold_core 兼容线上 FeatureSet，au9999_timing 支持盘前日频信号推理）",
    )
    label_mode: QlibLabelMode = Field(default="net", description="标签模式：net（扣费净收益）或 gross（原始收益率）")
    custom_expressions: dict[str, str] = Field(default_factory=dict, description="自定义因子表达式")
    horizons: list[HoldingPeriod] = Field(
        default_factory=lambda: [HoldingPeriod.D1, HoldingPeriod.W1],
        min_length=1,
        description="目标预测持有期列表",
    )
    window_mode: QlibWindowMode = Field(default="expanding", description="滚动验证窗口模式：expanding 或 rolling")
    train_window_bars: int | None = Field(
        default=None,
        ge=80,
        le=5000,
        description="rolling 模式下固定训练窗口长度（条数，如 750~1250 个交易日）",
    )
    step_bars: int | None = Field(
        default=None,
        ge=10,
        le=500,
        description="rolling 模式下每次滚动推进步长（条数，如 63~126 个交易日）",
    )
    use_robust_zscore: bool = Field(
        default=False,
        description="是否在每个训练折内按中位数与 MAD 执行 RobustZScoreNorm 与异常值裁剪",
    )
    n_splits: int = Field(default=3, ge=1, le=20, description="expanding 模式下的 Walk-Forward 滚动验证折数")
    num_boost_round: int = Field(default=80, ge=10, le=500, description="树模型迭代轮数")
    lgb_max_depth: int = Field(default=4, ge=2, le=8, description="LightGBM 最大树深（日频黄金建议 3~4）")
    lgb_num_leaves: int = Field(default=12, ge=4, le=31, description="LightGBM 最大叶节点数（建议 8~15）")
    lgb_reg_alpha: float = Field(default=1.0, ge=0.0, allow_inf_nan=False, description="LightGBM L1 正则系数")
    lgb_reg_lambda: float = Field(default=5.0, ge=0.0, allow_inf_nan=False, description="LightGBM L2 正则系数")
    confidence_level: float = Field(default=0.9, gt=0.5, lt=1.0, allow_inf_nan=False, description="残差下界置信水平")
    spread_pct: float = Field(default=0.002, gt=0.0, lt=0.05, allow_inf_nan=False, description="默认估算点差比例")
    output_model_dir: str | None = Field(default=None, min_length=1, description="可选导出 QlibAgent 产物目录")
    start_time: str | None = Field(default=None, description="可选训练起始时间")
    end_time: str | None = Field(default=None, description="可选训练结束时间")


class QlibTrainReport(BaseModel):
    """Qlib 训练、Walk-Forward 验证与实验登记综合报告。"""

    experiment: QlibExperimentRecord = Field(description="本次实验记录摘要")
    sample_counts: dict[str, int] = Field(default_factory=dict, description="各持有期有效样本数")
    walk_forward_folds: dict[str, list[WalkForwardFoldReport]] = Field(
        default_factory=dict,
        description="各持有期时间滚动样本外验证各折详情",
    )
    trained_model_info: dict[str, object] | None = Field(
        default=None,
        description="若导出可推理产物，则返回 QlibAgent 模型元信息",
    )


class QlibExperimentListResponse(BaseModel):
    """本地 Qlib 实验记录分页列表响应。"""

    total: int = Field(ge=0, description="已登记实验总数")
    experiments: list[QlibExperimentRecord] = Field(default_factory=list, description="按时间倒序排列的实验记录")


class QlibBacktestRequest(BaseModel):
    """单标的 Qlib 滚动预测阈值择时回测请求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_type: Literal["local_sqlite", "external_qlib"] = Field(default="local_sqlite", description="数据源类型")
    provider_uri: str | None = Field(default=None, min_length=1, description="可选覆盖数据目录路径")
    instrument: str = Field(default="SGE-Au99.99", min_length=1, description="单标的品种代码")
    freq: Literal["1h", "day"] = Field(default="day", description="回测频率")
    model_type: Literal["lgb", "linear"] = Field(default="lgb", description="滚动训练模型类型")
    factor_preset: QlibFactorPreset = Field(default="au9999_timing", description="因子预设")
    label_mode: QlibLabelMode = Field(default="net", description="标签模式：net 或 gross")
    custom_expressions: dict[str, str] = Field(default_factory=dict, description="自定义因子表达式")
    horizon: HoldingPeriod = Field(default=HoldingPeriod.D1, description="目标预测持有期")
    window_mode: QlibWindowMode = Field(default="rolling", description="滚动窗口模式")
    train_window_bars: int | None = Field(default=750, ge=80, le=5000, description="滚动训练窗长度（交易日）")
    step_bars: int | None = Field(default=63, ge=10, le=500, description="滚动步长（交易日）")
    use_robust_zscore: bool = Field(default=True, description="是否启用折内 RobustZScoreNorm")
    n_splits: int = Field(default=3, ge=1, le=20, description="expanding 模式折数")
    num_boost_round: int = Field(default=80, ge=10, le=500, description="LightGBM 迭代轮数")
    lgb_max_depth: int = Field(default=4, ge=2, le=8, description="LightGBM 最大树深")
    lgb_num_leaves: int = Field(default=12, ge=4, le=31, description="LightGBM 叶节点数")
    lgb_reg_alpha: float = Field(default=1.0, ge=0.0, allow_inf_nan=False, description="L1 正则系数")
    lgb_reg_lambda: float = Field(default=5.0, ge=0.0, allow_inf_nan=False, description="L2 正则系数")
    confidence_level: float = Field(default=0.9, gt=0.5, lt=1.0, allow_inf_nan=False, description="残差下界置信水平")
    buy_thresh: float = Field(default=0.001, allow_inf_nan=False, description="预测涨幅买入阈值")
    sell_thresh: float = Field(default=-0.0005, allow_inf_nan=False, description="预测涨幅平仓阈值")
    use_lcb_gate: bool = Field(default=False, description="买入时是否额外要求置信下界 net_return_lcb > 0")
    initial_cash: float = Field(default=100000.0, gt=0.0, allow_inf_nan=False, description="初始资金（元）")
    cash_utilization: float = Field(default=0.98, gt=0.0, le=1.0, allow_inf_nan=False, description="开仓资金使用比例")
    trade_unit_grams: float = Field(
        default=10.0, gt=0.0, allow_inf_nan=False, description="最小交易单位（Au99.99 为 10 克/手）"
    )
    open_cost: float = Field(default=0.0003, ge=0.0, lt=0.1, allow_inf_nan=False, description="开仓费率")
    close_cost: float = Field(default=0.0003, ge=0.0, lt=0.1, allow_inf_nan=False, description="平仓费率")
    limit_threshold: float | None = Field(
        default=0.08,
        gt=0.0,
        lt=1.0,
        allow_inf_nan=False,
        description="单日涨跌停拦截阈值（为空表示不限制）",
    )
    spread_pct: float = Field(default=0.002, gt=0.0, lt=0.05, allow_inf_nan=False, description="默认点差比例")
    start_time: str | None = Field(default=None, description="回测数据起始时间")
    end_time: str | None = Field(default=None, description="回测数据结束时间")

    @model_validator(mode="after")
    def _validate_thresholds(self) -> "QlibBacktestRequest":
        """校验买入阈值不低于平仓阈值，防止重叠区间信号冲突。"""
        if self.buy_thresh < self.sell_thresh:
            raise ValueError("买入阈值 buy_thresh 不得小于平仓阈值 sell_thresh")
        return self


class QlibBacktestReport(BaseModel):
    """单标的 Qlib 滚动择时回测报告。"""

    instrument: str = Field(description="回测标的")
    provider_uri: str = Field(description="实际数据目录")
    freq: str = Field(description="数据频率")
    horizon: HoldingPeriod = Field(description="预测持有期")
    factor_preset: QlibFactorPreset = Field(description="因子预设")
    window_mode: QlibWindowMode = Field(description="滚动窗口模式")
    summary: QlibBacktestSummary = Field(description="综合回测指标")
    walk_forward_folds: list[WalkForwardFoldReport] = Field(default_factory=list, description="各滚动折预测质量指标")
    trades: list[QlibBacktestTrade] = Field(default_factory=list, description="逐笔成交记录")
    equity_curve: list[QlibBacktestEquityPoint] = Field(default_factory=list, description="净值轨迹曲线")


class QlibSignalRequest(BaseModel):
    """基于已训练 Qlib 模型生成单标的盘前择时信号请求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_type: Literal["local_sqlite", "external_qlib"] = Field(default="local_sqlite", description="数据源类型")
    provider_uri: str | None = Field(default=None, min_length=1, description="可选覆盖数据目录路径")
    model_dir: str | None = Field(
        default=None, min_length=1, description="QlibAgent 模型目录（留空使用 QLIB_MODEL_DIR）"
    )
    instrument: str = Field(default="SGE-Au99.99", min_length=1, description="目标品种代码")
    freq: Literal["1h", "day"] = Field(default="day", description="特征数据频率")
    horizon: HoldingPeriod = Field(default=HoldingPeriod.D1, description="目标预测持有期")
    buy_thresh: float | None = Field(default=None, allow_inf_nan=False, description="可选覆盖买入阈值")
    sell_thresh: float | None = Field(default=None, allow_inf_nan=False, description="可选覆盖卖出阈值")
    use_lcb_gate: bool = Field(default=False, description="买入时是否要求 net_return_lcb > 0")
    publish_alert: bool = Field(default=False, description="是否将 BUY/SELL 信号推送至 AlertService 人工复核队列")
    trade_grams: float | None = Field(default=None, gt=0.0, allow_inf_nan=False, description="生成提醒时的建议克重")

    @model_validator(mode="after")
    def _validate_thresholds(self) -> "QlibSignalRequest":
        """校验同时传入的买入与平仓阈值满足 buy_thresh >= sell_thresh。"""
        if self.buy_thresh is not None and self.sell_thresh is not None and self.buy_thresh < self.sell_thresh:
            raise ValueError("买入阈值 buy_thresh 不得小于平仓阈值 sell_thresh")
        return self


class QlibSignalResponse(BaseModel):
    """Qlib 盘前择时信号响应。"""

    signal: QlibSignalRecord = Field(description="本次生成的择时信号详情")
