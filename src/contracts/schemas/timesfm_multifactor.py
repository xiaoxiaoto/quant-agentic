"""TimesFM 多因子数据准备、训练、回测与导出接口契约。"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from src.contracts.domains.benchmark import BacktestSummary
from src.contracts.domains.market_bar import KLineSource
from src.contracts.domains.timesfm_multifactor import (
    TimesFMDatasetManifest,
    TimesFMExperimentRecord,
    TimesFMFactorPreset,
    TimesFMFreq,
    TimesFMHeadModelType,
    TimesFMHorizonBacktestMetrics,
    TimesFMSourceMode,
    TimesFMTrainMode,
)
from src.contracts.enums.trading import HoldingPeriod
from src.contracts.schemas.model import WalkForwardFoldReport


class TimesFMStatusResponse(BaseModel):
    """TimesFM 多因子研究运行时状态总览。"""

    timesfm_enabled: bool = Field(description="配置中是否启用 TimesFM 推理")
    predictor_backend: str = Field(description="当前配置的预测后端")
    active_model_dir: str | None = Field(default=None, description="当前配置的线上模型目录")
    dataset_dir: str = Field(description="数据集根目录")
    experiment_dir: str = Field(description="实验记录根目录")
    experiment_count: int = Field(ge=0, description="已登记实验总数")
    latest_dataset: TimesFMDatasetManifest | None = Field(default=None, description="最近一份数据集清单")


class TimesFMPrepareRequest(BaseModel):
    """数据准备请求：从本地 SQLite 行情按 PIT 构造多因子数据集。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_mode: TimesFMSourceMode = Field(default="kline", description="行情来源：quotes / reference / kline")
    product_code: str | None = Field(default=None, min_length=1, description="标的编码，缺省取默认标的")
    freq: TimesFMFreq = Field(default="1h", description="等频序列频率")
    kline_interval: str | None = Field(default=None, min_length=1, description="kline 模式源周期，缺省随频率选择")
    kline_source: KLineSource | None = Field(default=None, description="kline 模式采集来源")
    factor_preset: TimesFMFactorPreset = Field(default="gold_multifactor", description="过去协变量因子预设")
    context_length: int = Field(default=512, ge=32, le=15360, description="上下文长度")
    stride: int = Field(default=1, ge=1, le=1000, description="样本采样步长")
    horizons: list[HoldingPeriod] = Field(
        default_factory=lambda: [HoldingPeriod.D1, HoldingPeriod.W1],
        min_length=1,
        description="目标持有期列表",
    )
    spread_pct: float = Field(default=0.002, gt=0.0, lt=0.05, allow_inf_nan=False, description="估算双边点差比例")
    exclude_synthetic: bool = Field(default=True, description="quotes 模式下是否排除合成报价")
    output_dir: str | None = Field(default=None, min_length=1, description="可选数据集输出目录")


class TimesFMPrepareReport(BaseModel):
    """数据准备结果。"""

    manifest: TimesFMDatasetManifest = Field(description="数据集清单")
    written_files: int = Field(ge=0, description="写入文件数")


class TimesFMTrainRequest(BaseModel):
    """训练、滚动评估与校准请求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dataset_dir: str | None = Field(default=None, min_length=1, description="数据集目录")
    experiment_name: str = Field(default="gold_timesfm_multifactor", min_length=1, max_length=120)
    train_mode: TimesFMTrainMode = Field(default="multifactor_head", description="训练模式")
    head_model_type: TimesFMHeadModelType = Field(default="lgb", description="融合头模型类型")
    horizons: list[HoldingPeriod] | None = Field(default=None, min_length=1, description="缺省使用数据集持有期")
    n_splits: int = Field(default=3, ge=1, le=10, description="Walk-Forward 折数")
    holdout_ratio: float = Field(default=0.2, ge=0.0, lt=0.5, allow_inf_nan=False, description="尾部独立留出比例")
    num_boost_round: int = Field(default=80, ge=10, le=500, description="树模型迭代轮数")
    confidence_level: float = Field(default=0.9, gt=0.5, lt=1.0, allow_inf_nan=False, description="下界置信水平")
    batch_size: int = Field(default=16, ge=1, le=64, description="TimesFM 批量推理大小")
    max_samples: int | None = Field(default=None, ge=100, description="每持有期最大训练样本数（均匀抽样）")
    use_covariates: bool = Field(default=True, description="是否使用协变量条件预测")
    finetune_epochs: int = Field(default=1, ge=1, le=20, description="微调轮数")
    finetune_lr: float = Field(default=1e-4, gt=0.0, allow_inf_nan=False, description="微调学习率")
    finetune_batch_size: int = Field(default=4, ge=1, le=32, description="微调批大小")
    unfreeze_last_layers: int = Field(default=0, ge=0, le=8, description="额外解冻的末尾 Transformer 层数")
    output_model_dir: str | None = Field(default=None, min_length=1, description="可选一步导出的模型目录")


class TimesFMTrainReport(BaseModel):
    """训练与滚动样本外评估报告。"""

    experiment: TimesFMExperimentRecord = Field(description="实验记录")
    sample_counts: dict[str, int] = Field(default_factory=dict, description="各持有期训练池样本数")
    walk_forward_folds: dict[str, list[WalkForwardFoldReport]] = Field(default_factory=dict)
    trained_model_info: dict[str, object] | None = Field(default=None, description="暂存产物模型元信息")


class TimesFMBacktestRequest(BaseModel):
    """严格样本外 PIT 回测请求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str | None = Field(default=None, min_length=1, description="实验运行标识，缺省取最近一次")
    model_dir: str | None = Field(default=None, min_length=1, description="显式模型目录（优先于 run_id）")
    dataset_dir: str | None = Field(default=None, min_length=1, description="回测数据集目录，缺省取实验数据集")
    initial_cash: float = Field(default=100000.0, gt=0.0, allow_inf_nan=False)
    trade_grams_per_order: float = Field(default=2.0, gt=0.0, allow_inf_nan=False)
    dca_step_interval: int = Field(default=5, ge=1)
    batch_size: int = Field(default=16, ge=1, le=64)
    start_time: AwareDatetime | None = Field(default=None, description="可选回测起始（不得早于校准结束）")
    end_time: AwareDatetime | None = Field(default=None, description="可选回测结束")

    @model_validator(mode="after")
    def _validate_time_range(self) -> "TimesFMBacktestRequest":
        """校验回测起始时间不得晚于结束时间。"""
        if self.start_time is not None and self.end_time is not None and self.start_time > self.end_time:
            raise ValueError("回测起始时间不得晚于结束时间")
        return self


class TimesFMBacktestReport(BaseModel):
    """严格样本外预测评估与交易层影子回测报告。"""

    run_id: str | None = Field(default=None, description="关联实验运行标识")
    model_info: dict[str, object] = Field(default_factory=dict, description="模型元信息")
    holdout_start_at: AwareDatetime = Field(description="样本外回测起始（晚于校准结束）")
    holdout_end_at: AwareDatetime = Field(description="样本外回测结束")
    horizon_metrics: dict[str, TimesFMHorizonBacktestMetrics] = Field(default_factory=dict)
    mapped_horizons: dict[str, str] = Field(default_factory=dict, description="缺失持有期到已训练持有期的映射")
    backtest_summary: BacktestSummary | None = Field(default=None, description="交易层影子回测汇总")


class TimesFMExportRequest(BaseModel):
    """不可变模型包导出请求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str | None = Field(default=None, min_length=1, description="实验运行标识，缺省取最近一次")
    output_model_dir: str = Field(min_length=1, description="导出目录")


class TimesFMExportReport(BaseModel):
    """不可变模型包导出结果。"""

    run_id: str = Field(description="导出的实验运行标识")
    output_model_dir: str = Field(description="导出目录")
    checksum_path: str = Field(description="SHA-256 校验文件路径")
    files: dict[str, str] = Field(default_factory=dict, description="相对路径到 SHA-256 的映射")
    model_info: dict[str, object] = Field(default_factory=dict, description="回读自检得到的模型元信息")
    verified: bool = Field(description="回读自检是否通过")


class TimesFMFullPipelineRequest(BaseModel):
    """数据准备、训练、回测与导出的一键流水线请求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prepare: TimesFMPrepareRequest = Field(default_factory=TimesFMPrepareRequest)
    train: TimesFMTrainRequest = Field(default_factory=TimesFMTrainRequest)
    backtest: TimesFMBacktestRequest = Field(default_factory=TimesFMBacktestRequest)
    output_model_dir: str | None = Field(default=None, min_length=1, description="可选导出目录")


class TimesFMFullPipelineReport(BaseModel):
    """一键流水线综合报告。"""

    prepare: TimesFMPrepareReport
    train: TimesFMTrainReport
    backtest: TimesFMBacktestReport
    export: TimesFMExportReport | None = None


class TimesFMExperimentListResponse(BaseModel):
    """实验记录列表。"""

    total: int = Field(ge=0)
    experiments: list[TimesFMExperimentRecord] = Field(default_factory=list)
