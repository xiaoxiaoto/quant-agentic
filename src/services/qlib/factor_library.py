"""Qlib 表达式因子库与银行扣费净收益标签生成器。"""

from __future__ import annotations

from typing import ClassVar, Literal

from src.contracts.domains.product import FeeRule
from src.contracts.domains.qlib import QlibFactorSpec
from src.contracts.enums.trading import HoldingPeriod


class QlibFactorLibrary:
    """管理黄金核心因子集、轻量 Alpha158 因子集与银行扣费净收益标签表达式。"""

    _HORIZON_STEPS_1H: ClassVar[dict[HoldingPeriod, int]] = {
        HoldingPeriod.H1: 1,
        HoldingPeriod.H4: 4,
        HoldingPeriod.D1: 24,
        HoldingPeriod.D3: 72,
        HoldingPeriod.W1: 168,
        HoldingPeriod.W2: 336,
        HoldingPeriod.M1: 720,
    }
    _HORIZON_STEPS_DAY: ClassVar[dict[HoldingPeriod, int]] = {
        HoldingPeriod.H1: 1,
        HoldingPeriod.H4: 1,
        HoldingPeriod.D1: 1,
        HoldingPeriod.D3: 3,
        HoldingPeriod.W1: 5,
        HoldingPeriod.W2: 10,
        HoldingPeriod.M1: 21,
    }

    @classmethod
    def _gold_core_specs(cls, freq: Literal["1h", "day"]) -> list[QlibFactorSpec]:
        step_1d = 24 if freq == "1h" else 1
        step_3d = 72 if freq == "1h" else 3
        step_1w = 168 if freq == "1h" else 5
        vol_scale = "93.5948" if freq == "1h" else "19.1049"
        vol_win = 24 if freq == "1h" else 20
        return [
            QlibFactorSpec(
                name="return_1d",
                expression=f"($bid / Ref($bid, {step_1d}) - 1)",
                description="近 1 日动量收益率（与 FeatureSet.return_1d 对齐）",
            ),
            QlibFactorSpec(
                name="return_3d",
                expression=f"($bid / Ref($bid, {step_3d}) - 1)",
                description="近 3 日动量收益率（与 FeatureSet.return_3d 对齐）",
            ),
            QlibFactorSpec(
                name="return_1w",
                expression=f"($bid / Ref($bid, {step_1w}) - 1)",
                description="近 1 周动量收益率（与 FeatureSet.return_1w 对齐）",
            ),
            QlibFactorSpec(
                name="realized_volatility",
                expression=f"(Std($bid / Ref($bid, 1) - 1, {vol_win}) * {vol_scale})",
                description="近期已实现年化波动率",
            ),
            QlibFactorSpec(
                name="atr",
                expression="Std($bid, 20)",
                description="近 20 期价格标准差波幅代理",
            ),
            QlibFactorSpec(
                name="spread",
                expression="$spread",
                description="银行实时买卖点差",
            ),
            QlibFactorSpec(
                name="spread_pct",
                expression="$spread_pct",
                description="点差占中间价比例",
            ),
            QlibFactorSpec(
                name="bank_basis",
                expression="$bank_basis",
                description="银行报价相对基准金价基差",
            ),
            QlibFactorSpec(
                name="hours_to_weekend_close",
                expression="$hours_to_weekend_close",
                description="距离周末闭市剩余小时数",
            ),
            QlibFactorSpec(
                name="hours_since_open",
                expression="$hours_since_open",
                description="距离本周开盘已过小时数",
            ),
            QlibFactorSpec(
                name="is_weekend_risk_window",
                expression="$is_weekend_risk_window",
                description="是否处于周末闭市风控窗口",
            ),
        ]

    @classmethod
    def _alpha158_lite_specs(cls) -> list[QlibFactorSpec]:
        return [
            QlibFactorSpec(name="kmid", expression="($close - $open) / $open", description="K 线实体涨跌幅"),
            QlibFactorSpec(name="klen", expression="($high - $low) / $open", description="K 线振幅占比"),
            QlibFactorSpec(name="roc_5", expression="Ref($close, 5) / $close - 1", description="5 期价格变化率"),
            QlibFactorSpec(name="roc_10", expression="Ref($close, 10) / $close - 1", description="10 期价格变化率"),
            QlibFactorSpec(name="roc_20", expression="Ref($close, 20) / $close - 1", description="20 期价格变化率"),
            QlibFactorSpec(name="ma_5", expression="Mean($close, 5) / $close", description="5 期均线偏离度"),
            QlibFactorSpec(name="ma_20", expression="Mean($close, 20) / $close", description="20 期均线偏离度"),
            QlibFactorSpec(name="std_10", expression="Std($close, 10) / $close", description="10 期相对波动率"),
            QlibFactorSpec(name="std_20", expression="Std($close, 20) / $close", description="20 期相对波动率"),
            QlibFactorSpec(
                name="rsv_20",
                expression="($close - Min($low, 20)) / (Max($high, 20) - Min($low, 20) + 1e-12)",
                description="20 期价格通道位置 RSV",
            ),
        ]

    @classmethod
    def horizon_steps(cls, horizon: HoldingPeriod, freq: Literal["1h", "day"]) -> int:
        """返回目标持有期在给定数据频率下的前瞻步数。"""
        table = cls._HORIZON_STEPS_1H if freq == "1h" else cls._HORIZON_STEPS_DAY
        return table[horizon]

    @classmethod
    def get_factors(
        cls,
        preset: Literal["gold_core", "alpha158_lite", "custom"],
        *,
        freq: Literal["1h", "day"],
        custom_expressions: dict[str, str] | None = None,
    ) -> list[QlibFactorSpec]:
        """根据预设名称与可选自定义表达式返回 Qlib 因子定义列表。"""
        if preset == "gold_core":
            return cls._gold_core_specs(freq)
        if preset == "alpha158_lite":
            return cls._alpha158_lite_specs()
        if not custom_expressions:
            raise ValueError("custom 因子预设必须提供至少一个自定义表达式")
        return [
            QlibFactorSpec(name=name.strip(), expression=expr.strip(), description="用户自定义 Qlib 表达式因子")
            for name, expr in custom_expressions.items()
            if name.strip() and expr.strip()
        ]

    @classmethod
    def build_net_return_label_expression(
        cls,
        *,
        horizon: HoldingPeriod,
        freq: Literal["1h", "day"],
        fee_rule: FeeRule,
        source_type: Literal["local_sqlite", "external_qlib"],
        spread_pct: float = 0.002,
    ) -> str:
        """构造扣除双边点差与买卖手续费的前瞻净收益标签表达式。"""
        step = cls.horizon_steps(horizon, freq)
        buy_mult = 1.0 + fee_rule.buy_fee_rate
        sell_mult = 1.0 - fee_rule.sell_fee_rate
        if source_type == "local_sqlite":
            return f"((Ref($bid, -{step}) * {sell_mult:.8f}) / ($ask * {buy_mult:.8f}) - 1)"
        half = spread_pct / 2.0
        entry_mult = (1.0 + half) * buy_mult
        exit_mult = (1.0 - half) * sell_mult
        return f"((Ref($close, -{step}) * {exit_mult:.8f}) / ($close * {entry_mult:.8f}) - 1)"
