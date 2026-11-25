"""真实模型服务的请求与响应契约。"""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from src.contracts.domains.event import MacroCalendarEvent, NewsEvent
from src.contracts.enums.trading import HoldingPeriod


class NewsExtractRequest(BaseModel):
    """来源元数据由调用方提供，模型只提取风险属性。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=1000)
    content: str = Field(min_length=1, max_length=100000)
    source: str = Field(min_length=1, max_length=1000)
    published_at: AwareDatetime
    event_id: str | None = Field(default=None, min_length=1, max_length=200)


class NewsExtractResponse(BaseModel):
    """经模型校验并录入共享风险引擎的新闻结果。"""

    event: NewsEvent
    risk_flags: list[str]


class NewsSyncReport(BaseModel):
    """京东黄金快讯自动同步至新闻风险引擎的批次结果报告。"""

    fetched_count: int = Field(default=0, ge=0, description="本轮从京东快讯解析到的条目数")
    ingested_count: int = Field(default=0, ge=0, description="成功提取并录入新闻风险引擎的新增条目数")
    skipped_count: int = Field(default=0, ge=0, description="因已存在去重或字段缺失而跳过的条目数")
    failed_count: int = Field(default=0, ge=0, description="大模型提取或录入失败的条目数")
    triggered_risk_flags: list[str] = Field(default_factory=list, description="本轮新增触发的高危新闻风控标志")
    synced_at: datetime = Field(description="本轮同步完成时间戳")


class NewsOverviewResponse(BaseModel):
    """消息面态势总览：包含宏观事件锁、当前生效风控标志及已提取的新闻与日程列表。"""

    event_locked: bool = Field(description="当前是否处于重大宏观日程禁入锁定窗口")
    lock_reason: str | None = Field(default=None, description="锁定原因说明")
    active_risk_flags: list[str] = Field(default_factory=list, description="当前生效的新闻与宏观风控标志")
    news_events: list[NewsEvent] = Field(default_factory=list, description="按时间倒序排列的结构化新闻风险事件")
    macro_events: list[MacroCalendarEvent] = Field(default_factory=list, description="已登记的重大宏观经济日程")


class MacroEventCreateRequest(BaseModel):
    """登记重大宏观经济日程请求载荷。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str | None = Field(default=None, min_length=1, max_length=120)
    event_name: str = Field(min_length=1, max_length=200)
    scheduled_at: AwareDatetime
    lock_window_hours_before: float = Field(default=2.0, ge=0.0, le=72.0, allow_inf_nan=False)
    lock_window_hours_after: float = Field(default=2.0, ge=0.0, le=72.0, allow_inf_nan=False)


class WalkForwardFoldReport(BaseModel):
    """单个时间滚动窗口（Walk-Forward）的样本外验证指标。"""

    fold_index: int = Field(ge=1, description="滚动折次编号（从 1 开始）")
    train_count: int = Field(ge=0, description="清除跨边界标签后的训练集样本数")
    test_count: int = Field(ge=1, description="样本外测试集样本数")
    test_start_at: datetime = Field(description="测试集起始时间戳")
    test_end_at: datetime = Field(description="测试集结束时间戳")
    mae: float = Field(ge=0.0, description="样本外净收益平均绝对误差")
    directional_accuracy: float = Field(ge=0.0, le=1.0, description="样本外盈利方向判定准确率")
    brier_score: float = Field(ge=0.0, le=1.0, description="样本外校准概率 Brier 分数")
    mean_predicted_return: float = Field(description="样本外平均预测净收益率")
    mean_actual_return: float = Field(description="样本外平均实际净收益率")
    lcb_coverage: float = Field(ge=0.0, le=1.0, description="实际净收益不低于置信下界 LCB 的覆盖率")


class TrainingPipelineRequest(BaseModel):
    """PIT 训练样本生成、Walk-Forward 评估与 LightGBM 训练闭环请求。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_code: str | None = Field(default=None, min_length=1, description="双边报价标的编码")
    reference_product_code: str | None = Field(
        default=None,
        min_length=1,
        description="可选：改用已持久化的 CNY/g 参考行情按估算点差构造研究样本",
    )
    spread_pct: float = Field(default=0.002, gt=0.0, lt=0.05, allow_inf_nan=False, description="参考行情估算点差比例")
    horizons: list[HoldingPeriod] = Field(
        default_factory=lambda: [HoldingPeriod.D1, HoldingPeriod.W1],
        min_length=1,
        description="目标预测持有期列表",
    )
    n_splits: int = Field(default=3, ge=1, le=10, description="Walk-Forward 时间滚动验证折数")
    num_boost_round: int = Field(default=80, ge=10, le=500, description="LightGBM 迭代轮数")
    confidence_level: float = Field(default=0.9, gt=0.5, lt=1.0, allow_inf_nan=False, description="残差下界置信水平")
    export_samples_path: str | None = Field(default=None, min_length=1, description="可选导出 PIT 样本 JSONL 路径")
    output_model_dir: str | None = Field(default=None, min_length=1, description="可选输出最终 LightGBM 模型产物目录")
    exclude_synthetic: bool = Field(default=True, description="使用双边报价库时是否排除模拟合成报价")


class TrainingPipelineReport(BaseModel):
    """PIT 样本构建、样本外滚动验证与模型训练综合报告。"""

    product_code: str = Field(description="样本来源标的编码")
    source_mode: Literal["quotes", "reference"] = Field(description="行情来源模式：双边报价库或参考行情库")
    horizons: list[HoldingPeriod] = Field(description="本次构建与评估的持有期列表")
    sample_counts: dict[str, int] = Field(default_factory=dict, description="各持有期生成的有效 PIT 样本数")
    samples_output_path: str | None = Field(default=None, description="导出的 JSONL 样本文件路径")
    model_output_dir: str | None = Field(default=None, description="最终训练落盘的模型产物目录")
    walk_forward_folds: dict[str, list[WalkForwardFoldReport]] = Field(
        default_factory=dict,
        description="各持有期时间滚动样本外验证各折详细指标",
    )
    oos_summary: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="各持有期样本外验证聚合指标摘要",
    )
    trained_model_info: dict[str, object] | None = Field(
        default=None,
        description="若输出了最终模型产物，则返回模型元信息与校准摘要",
    )
