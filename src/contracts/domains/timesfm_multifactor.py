"""TimesFM 多因子模型的数据集、分布特征、微调与实验记录领域模型。"""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from src.contracts.enums.trading import HoldingPeriod

TimesFMFactorPreset = Literal["gold_multifactor", "gold_ohlcv_multifactor"]
TimesFMTrainMode = Literal["multifactor_head", "backbone_finetune"]
TimesFMHeadModelType = Literal["lgb", "ridge"]
TimesFMSourceMode = Literal["quotes", "reference", "kline"]
TimesFMFreq = Literal["1h", "day"]


class TimesFMForecastDistribution(BaseModel):
    """单次多因子条件预测的收益分布特征（均以决策时点最新买入价为基准）。"""

    point_return: float = Field(description="终点中位数预测收益率")
    q10_return: float = Field(description="终点最低分位预测收益率")
    q90_return: float = Field(description="终点最高分位预测收益率")
    iqr_return: float = Field(description="终点近似四分位距（就近分位）")
    path_mean_return: float = Field(description="预测路径均值收益率")
    path_min_return: float = Field(description="预测路径最低收益率")
    skew: float = Field(description="终点分位偏度代理（-1 到 1）")


class TimesFMDistributionProvider(Protocol):
    """可按决策时点与持有期提供已缓存 TimesFM 分布特征的对象协议。"""

    def get_cached_distribution(self, as_of: datetime, horizon: HoldingPeriod) -> TimesFMForecastDistribution | None:
        """返回与决策时点对齐的分布特征，未命中返回 None。"""
        ...


class TimesFMDatasetManifest(BaseModel):
    """TimesFM 多因子数据集目录元信息清单。"""

    dataset_id: str = Field(description="数据集唯一标识")
    dataset_dir: str = Field(description="数据集目录绝对路径")
    source_mode: TimesFMSourceMode = Field(description="行情来源模式")
    product_code: str = Field(description="标的编码")
    freq: TimesFMFreq = Field(description="等频序列频率")
    factor_preset: TimesFMFactorPreset = Field(description="过去协变量因子预设")
    context_length: int = Field(ge=32, description="上下文长度")
    stride: int = Field(ge=1, description="样本采样步长")
    horizons: list[HoldingPeriod] = Field(description="目标持有期列表")
    horizon_steps: dict[str, int] = Field(default_factory=dict, description="各持有期对应的前瞻步数")
    total_bars: int = Field(ge=0, description="对齐后的序列条数")
    sample_counts: dict[str, int] = Field(default_factory=dict, description="各持有期有效样本数")
    past_only_factors: list[str] = Field(default_factory=list, description="过去协变量因子名称")
    past_future_factors: list[str] = Field(default_factory=list, description="未来已知日历协变量名称")
    tabular_factors: list[str] = Field(default_factory=list, description="与 FeatureSet 对齐的截面因子名称")
    start_time: str | None = Field(default=None, description="最早序列时刻")
    end_time: str | None = Field(default=None, description="最晚序列时刻")
    spread_pct: float | None = Field(default=None, description="参考价合成估算点差比例")
    buy_fee_rate: float = Field(description="买入费率")
    sell_fee_rate: float = Field(description="卖出费率")
    created_at: datetime = Field(description="数据集生成时间")


class TimesFMFinetuneSummary(BaseModel):
    """主干模型微调摘要。"""

    enabled: bool = Field(default=False, description="是否执行了微调")
    epochs: int = Field(default=0, ge=0, description="微调轮数")
    learning_rate: float = Field(default=0.0, ge=0.0, description="学习率")
    unfreeze_last_layers: int = Field(default=0, ge=0, description="额外解冻的末尾 Transformer 层数")
    trainable_params: int = Field(default=0, ge=0, description="可训练参数量")
    total_params: int = Field(default=0, ge=0, description="总参数量")
    sample_count: int = Field(default=0, ge=0, description="微调窗口数")
    initial_loss: float | None = Field(default=None, description="首个批次分位损失")
    final_loss: float | None = Field(default=None, description="末轮平均分位损失")
    checkpoint_saved: bool = Field(default=False, description="是否已保存微调权重")


class TimesFMHorizonBacktestMetrics(BaseModel):
    """单个持有期的严格样本外预测评估指标。"""

    horizon: HoldingPeriod = Field(description="持有期")
    sample_count: int = Field(ge=0, description="样本外样本数")
    mae: float = Field(ge=0.0, description="净收益平均绝对误差")
    directional_accuracy: float = Field(ge=0.0, le=1.0, description="盈利方向准确率")
    brier_score: float = Field(ge=0.0, le=1.0, description="校准概率 Brier 分数")
    lcb_coverage: float = Field(ge=0.0, le=1.0, description="实际净收益不低于置信下界的覆盖率")
    ic: float | None = Field(default=None, description="皮尔逊信息系数")
    rank_ic: float | None = Field(default=None, description="斯皮尔曼秩信息系数")
    mean_predicted_return: float = Field(description="平均预测净收益率")
    mean_actual_return: float = Field(description="平均实际净收益率")


class TimesFMExperimentRecord(BaseModel):
    """单次 TimesFM 多因子训练的实验元数据记录。"""

    experiment_name: str = Field(description="实验名称")
    run_id: str = Field(description="唯一运行标识")
    dataset_id: str = Field(description="使用的数据集标识")
    dataset_dir: str = Field(description="使用的数据集目录")
    product_code: str = Field(description="标的编码")
    freq: TimesFMFreq = Field(description="序列频率")
    train_mode: TimesFMTrainMode = Field(description="训练模式")
    head_model_type: TimesFMHeadModelType = Field(description="融合头模型类型")
    factor_preset: TimesFMFactorPreset = Field(description="因子预设")
    use_covariates: bool = Field(description="是否使用协变量条件预测")
    context_length: int = Field(ge=32, description="上下文长度")
    horizons: list[HoldingPeriod] = Field(description="训练的持有期列表")
    holdout_ratio: float = Field(ge=0.0, lt=1.0, description="尾部独立留出比例")
    holdout_start_at: datetime | None = Field(default=None, description="留出区起始时刻")
    holdout_end_at: datetime | None = Field(default=None, description="留出区结束时刻")
    oos_summary: dict[str, dict[str, float]] = Field(default_factory=dict, description="各持有期滚动样本外摘要")
    finetune_summary: TimesFMFinetuneSummary = Field(default_factory=TimesFMFinetuneSummary, description="微调摘要")
    staging_dir: str = Field(description="暂存产物目录")
    exported_model_dir: str | None = Field(default=None, description="已导出的不可变模型目录")
    created_at: datetime = Field(description="实验记录创建时间")
