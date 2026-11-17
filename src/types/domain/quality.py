"""数据采集质量与行情特征统计领域模型。

用于评估数据源时延、缺口、点差分布及周末跳空风险。
"""

from datetime import datetime

from pydantic import BaseModel, Field


class WeekendGapRecord(BaseModel):
    """单个周末收盘与开盘价格跳空记录。"""

    close_time: datetime = Field(
        description="周末闭市时间戳",
    )
    open_time: datetime = Field(
        description="周一开市时间戳",
    )
    close_mid_price: float = Field(
        description="闭市中间价（元/克）",
    )
    open_mid_price: float = Field(
        description="开市中间价（元/克）",
    )
    gap_amount: float = Field(
        description="开盘与收盘价差：open - close（元/克）",
    )
    gap_pct: float = Field(
        description="跳空幅度百分比：(open - close) / close",
    )


class CollectorQualityReport(BaseModel):
    """采集器数据质量与点差统计报告。"""

    report_id: str = Field(
        description="质量分析报告唯一标识",
    )
    start_time: datetime = Field(
        description="统计分析区间起始时间",
    )
    end_time: datetime = Field(
        description="统计分析区间截止时间",
    )
    total_records: int = Field(
        description="区间内采集的原始报价总记录数",
    )
    gap_count: int = Field(
        default=0,
        description="连续交易时段内异常缺口次数",
    )
    max_gap_seconds: float = Field(
        default=0.0,
        description="最大单次数据缺口持续时间（秒）",
    )
    avg_latency_ms: float = Field(
        default=0.0,
        description="采样平均传输时延（毫秒）",
    )
    p95_latency_ms: float = Field(
        default=0.0,
        description="采样 95 分位传输时延（毫秒）",
    )
    spread_mean: float = Field(
        default=0.0,
        description="买卖点差平均值（元/克）",
    )
    spread_median: float = Field(
        default=0.0,
        description="买卖点差中位数（元/克）",
    )
    spread_p95: float = Field(
        default=0.0,
        description="买卖点差 95 分位数（元/克）",
    )
    spread_p99: float = Field(
        default=0.0,
        description="买卖点差 99 分位数（元/克）",
    )
    weekend_gaps: list[WeekendGapRecord] = Field(
        default_factory=list,
        description="周末跳空事件记录列表",
    )
    max_weekend_gap_pct: float = Field(
        default=0.0,
        description="最大周末跳空幅度百分比",
    )
    mean_weekend_gap_pct: float = Field(
        default=0.0,
        description="平均周末跳空幅度百分比",
    )
