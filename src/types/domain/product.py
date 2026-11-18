"""产品规则领域类型。

所有产品规则均为带版本的动态参数，严禁硬编码为不可变常量。
上线或实盘运行前必须根据银行/合作平台的当日官方公告进行核对。
"""

from datetime import datetime

from pydantic import BaseModel, Field


class FeeRule(BaseModel):
    """带版本追踪的费率规则。

    买入手续费与卖出手续费为用户提供的初始快照。
    用于实盘决策前必须经官方渠道核实。
    """

    buy_fee_rate: float = Field(
        default=0.0,
        ge=0.0,
        allow_inf_nan=False,
        description="买入费率（当前优惠费率为 0%，可随公告调整）",
    )
    sell_fee_rate: float = Field(
        default=0.004,
        lt=1.0,
        ge=0.0,
        allow_inf_nan=False,
        description="卖出费率（0.4% = 40bp，以卖出成交金额为基数扣除）",
    )
    effective_from: datetime | None = Field(
        default=None,
        description="费率规则生效起始时间",
    )
    effective_to: datetime | None = Field(
        default=None,
        description="费率规则失效截止时间",
    )
    announcement_id: str | None = Field(
        default=None,
        description="银行官方费率公告编号",
    )
    observed_at: datetime | None = Field(
        default=None,
        description="系统核实观测时间戳",
    )
    rule_version: str = Field(
        default="snapshot_user_v1",
        description="费率规则版本号",
    )


class TradeRule(BaseModel):
    """包含碎片库存处理的交易规则。

    碎片库存规则：持仓 > 1克时，单笔卖出至少 1克；持仓 ≤ 1克时，必须一次性全额卖出。
    """

    buy_min_grams: float = Field(
        default=1.0,
        description="买入最小克数：1 克",
    )
    sell_min_grams: float = Field(
        default=1.0,
        description="持仓大于 1克时的单笔最小卖出克数：1 克",
    )
    liquidate_all_if_le_1g: bool = Field(
        default=True,
        description="持仓小于等于 1克时是否必须一次性全部卖出",
    )
    gram_precision: int | None = Field(
        default=None,
        description="克重交易与记账小数精度（以银行实测回报为准）",
    )
    rule_version: str = Field(
        default="snapshot_user_v1",
        description="交易规则版本号",
    )


class InterestRule(BaseModel):
    """积存黄金利息参数。

    每克每日活期利息 ≈ 克数 × 0.0002 / 360 ≈ 克数 × 5.56e-7。
    达到 0.0001 克发放门槛约需 180 / 持仓克数 天。
    相对 0.4% 的卖出费可忽略，回测与标签中默认记为 0。
    """

    annual_rate: float = Field(
        default=0.0002,
        description="活期年化利率 0.02%，按 360 天基数计算",
    )
    day_basis: int = Field(
        default=360,
        description="计息基数天数（通常为 360 天）",
    )
    credit_threshold_grams: float = Field(
        default=0.0001,
        description="利息发放到账的最小克数门槛",
    )
    backtest_rate: float = Field(
        default=0.0,
        description="回测与特征标签中默认利息记为 0",
    )


class SessionConfig(BaseModel):
    """交易时段配置。

    连续交易时段：周一 09:00 至周六 02:30（北京时间）。
    周末休市：约 54.5 小时（周六 02:30 至周一 09:00），遇节假日依公告调整。
    """

    timezone: str = Field(
        default="Asia/Shanghai",
        description="交易时区（默认北京时间）",
    )
    week_open_day: int = Field(
        default=0,
        description="周开市日：周一=0",
    )
    week_open_hour: int = Field(
        default=9,
        description="周开市起始小时（09:00）",
    )
    week_open_minute: int = Field(
        default=0,
        description="周开市起始分钟",
    )
    week_close_day: int = Field(
        default=5,
        description="周闭市日：周六=5",
    )
    week_close_hour: int = Field(
        default=2,
        description="周闭市结束小时（02:30）",
    )
    week_close_minute: int = Field(
        default=30,
        description="周闭市结束分钟",
    )
    rule_version: str = Field(
        default="snapshot_user_v1",
        description="交易时段配置版本号",
    )


class ProductConfig(BaseModel):
    """复合产品配置——全参数版本化。

    verified_with_bank 在生产/真实交易使用前必须置为 True。
    """

    instrument_id: str = Field(
        default="GOLD_ACCUMULATION",
        description="交易标的产品代码",
    )
    fee_rule: FeeRule = Field(
        default_factory=FeeRule,
        description="生效的手续费规则",
    )
    trade_rule: TradeRule = Field(
        default_factory=TradeRule,
        description="生效的买卖交易规则",
    )
    interest_rule: InterestRule = Field(
        default_factory=InterestRule,
        description="生效的黄金计息规则",
    )
    session_config: SessionConfig = Field(
        default_factory=SessionConfig,
        description="生效的交易时段配置",
    )
    config_version: str = Field(
        default="v5.0_snapshot",
        description="复合配置总体版本号",
    )
    verified_with_bank: bool = Field(
        default=False,
        description="实盘前是否已与银行官方规则核实确认",
    )
