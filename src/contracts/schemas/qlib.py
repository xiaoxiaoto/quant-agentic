"""Qlib 离线量化研究、数据导出、因子评估与训练接口契约。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.contracts.domains.qlib import QlibDatasetManifest, QlibExperimentRecord, QlibFactorMetric
from src.contracts.enums.trading import HoldingPeriod
from src.contracts.schemas.model import WalkForwardFoldReport


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
    freq: Literal["1h", "day"] = Field(default="1h", description="导出重采样频率")
    kline_interval: str = Field(default="m60", min_length=1, description="kline 模式下的源 K 线周期（如 m60/day）")
    spread_pct: float = Field(default=0.002, gt=0.0, lt=0.05, allow_inf_nan=False, description="估算双边点差比例")
    exclude_synthetic: bool = Field(default=True, description="quotes 模式下是否排除模拟合成报价")
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
    factor_preset: Literal["gold_core", "alpha158_lite", "custom"] = Field(
        default="gold_core",
        description="因子预设组：gold_core / alpha158_lite / custom",
    )
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
    factor_preset: Literal["gold_core", "alpha158_lite", "custom"] = Field(
        default="gold_core",
        description="因子预设（仅 gold_core 可导出兼容线上 FeatureSet 的推理产物）",
    )
    custom_expressions: dict[str, str] = Field(default_factory=dict, description="自定义因子表达式")
    horizons: list[HoldingPeriod] = Field(
        default_factory=lambda: [HoldingPeriod.D1, HoldingPeriod.W1],
        min_length=1,
        description="目标预测持有期列表",
    )
    n_splits: int = Field(default=3, ge=1, le=10, description="Walk-Forward 滚动验证折数")
    num_boost_round: int = Field(default=80, ge=10, le=500, description="树模型迭代轮数")
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
