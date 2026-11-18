"""经济可行性分析引擎。

核心目标：用最小的先验成本回答——在扣除卖出费与买卖点差后，
是否存在可盈利的交易周期空间。

本分析必须在任何复杂模型或自动交易工程之前通过。
若评估不通过，系统将降级为提醒与对账工具或直接终止。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.services.cost_calculator import CostCalculator
from src.types.domain.product import ProductConfig

# ── 持有周期与交易小时数映射 ──────────────────────────────────────────

# 各周期折算有效交易小时数：
# 1d ≈ 1 个交易日 = 113.5h / 5 ≈ 22.7h。
# 1w = 周一 09:00 至周六 02:30 = 113.5h。
HOLDING_PERIOD_TRADING_HOURS: dict[str, float] = {
    "30min": 0.5,
    "1h": 1.0,
    "4h": 4.0,
    "1d": 22.7,
    "3d": 68.1,
    "1w": 113.5,
    "2w": 227.0,
    "1m": 454.0,
}


# ── 分析结果数据结构 ──────────────────────────────────────────────────


@dataclass
class FeasibilityRow:
    """单一 持有期 × 点差假设 组合的测算结果行。"""

    holding_period: str
    trading_hours: float
    sigma_h: float
    typical_move_m: float
    spread_pct: float
    total_cost_pct: float
    required_accuracy: float
    assessment: str


@dataclass
class FeasibilityReport:
    """完整可行性评估报告。

    若没有任一持有期评估达到 "marginal"（边缘可行）或 "possible"（可行区间），
    则经济可行性判定不通过。
    """

    annual_volatility: float
    trading_hours_per_year: float
    buy_fee_rate: float
    sell_fee_rate: float
    spread_scenarios: list[float]
    rows: list[FeasibilityRow] = field(default_factory=list)

    def has_viable_period(self) -> bool:
        """检查是否存在至少一个处于边缘可行或可行区间的持有周期。"""
        return any(r.assessment in ("marginal", "possible") for r in self.rows)

    def render_table(self) -> str:
        """渲染为 Markdown 对照表格。"""
        lines: list[str] = []
        lines.append("| Holding Period | Trading Hours | σ_h | Move m | Spread | Cost C | Required p* | Assessment |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
        for r in self.rows:
            p_str = f"{r.required_accuracy:.1%}" if r.required_accuracy <= 1.0 else ">100%"
            lines.append(
                f"| {r.holding_period} | {r.trading_hours:.1f} | "
                f"{r.sigma_h:.4%} | {r.typical_move_m:.4%} | "
                f"{r.spread_pct:.2%} | {r.total_cost_pct:.2%} | "
                f"{p_str} | {r.assessment} |"
            )
        return "\n".join(lines)


# ── 可行性分析器 ──────────────────────────────────────────────────────


class FeasibilityAnalyzer:
    """经济可行性分析器。

    通过数学公式对不同周期与点差情景计算保本所需准确率。
    纯分析与先验计算，不依赖历史清洗数据与复杂机器学习模型。
    """

    _DEFAULT_HOLDING_PERIODS = ("1h", "4h", "1d", "3d", "1w", "2w", "1m")
    _DEFAULT_SPREAD_SCENARIOS = (0.0005, 0.001, 0.002, 0.003)

    def __init__(
        self,
        product_config: ProductConfig | None = None,
    ) -> None:
        self._config = product_config or ProductConfig()
        self._cost_calc = CostCalculator(self._config.fee_rule)

    def _assess(self, required_accuracy: float) -> str:
        """根据所需准确率判定可行性等级。"""
        if required_accuracy > 1.0:
            return "impossible"
        if required_accuracy > 0.75:
            return "very_hard"
        if required_accuracy > 0.65:
            return "hard"
        if required_accuracy > 0.55:
            return "marginal"
        return "possible"

    def analyze(
        self,
        annual_volatility: float = 0.17,
        trading_hours_per_year: float = 6318.0,
        holding_periods: tuple[str, ...] | None = None,
        spread_scenarios: tuple[float, ...] | None = None,
    ) -> FeasibilityReport:
        """运行全量经济可行性测算。"""
        periods = holding_periods or self._DEFAULT_HOLDING_PERIODS
        spreads = spread_scenarios or self._DEFAULT_SPREAD_SCENARIOS
        fee = self._config.fee_rule

        report = FeasibilityReport(
            annual_volatility=annual_volatility,
            trading_hours_per_year=trading_hours_per_year,
            buy_fee_rate=fee.buy_fee_rate,
            sell_fee_rate=fee.sell_fee_rate,
            spread_scenarios=list(spreads),
        )

        for period_label in periods:
            hours = HOLDING_PERIOD_TRADING_HOURS[period_label]
            sigma_h = annual_volatility * math.sqrt(hours / trading_hours_per_year)
            move_m = CostCalculator.typical_absolute_move(annual_volatility, hours, trading_hours_per_year)

            for spread in spreads:
                total_cost = self._cost_calc.total_round_trip_cost_pct(spread)
                p_star = self._cost_calc.required_accuracy(total_cost, move_m)

                report.rows.append(
                    FeasibilityRow(
                        holding_period=period_label,
                        trading_hours=hours,
                        sigma_h=sigma_h,
                        typical_move_m=move_m,
                        spread_pct=spread,
                        total_cost_pct=total_cost,
                        required_accuracy=p_star,
                        assessment=self._assess(p_star),
                    )
                )

        return report

    def analyze_single(
        self,
        holding_period: str,
        spread_pct: float,
        annual_volatility: float = 0.17,
        trading_hours_per_year: float = 6318.0,
    ) -> FeasibilityRow:
        """测算单个 持有周期 × 点差假设 的可行性行。"""
        return self.analyze(
            annual_volatility=annual_volatility,
            trading_hours_per_year=trading_hours_per_year,
            holding_periods=(holding_period,),
            spread_scenarios=(spread_pct,),
        ).rows[0]
