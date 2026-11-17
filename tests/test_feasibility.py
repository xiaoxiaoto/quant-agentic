"""经济可行性分析引擎单元测试。"""

import pytest

from src.services.feasibility import (
    HOLDING_PERIOD_TRADING_HOURS,
    FeasibilityAnalyzer,
)


class TestFeasibilityAnalyzer:
    """可行性分析器关键结论复核。"""

    def test_30min_impossible(self) -> None:
        """30 分钟窗口：所需准确率 > 100%，判定为 impossible。"""
        analyzer = FeasibilityAnalyzer()
        row = analyzer.analyze_single("30min", spread_pct=0.001)
        assert row.required_accuracy > 1.0
        assert row.assessment == "impossible"

    def test_1day_very_hard(self) -> None:
        """1 日周期：往返成本 0.5% 下所需准确率 p* ≈ 81%，判定为 very_hard。"""
        analyzer = FeasibilityAnalyzer()
        row = analyzer.analyze_single("1d", spread_pct=0.001)
        assert row.total_cost_pct == pytest.approx(0.005)
        assert row.required_accuracy == pytest.approx(0.81, abs=0.03)
        assert row.assessment == "very_hard"

    def test_1week_marginal(self) -> None:
        """1 周周期：所需准确率 p* ≈ 64%，判定为 marginal（处于 0.55~0.65 区间）。"""
        analyzer = FeasibilityAnalyzer()
        row = analyzer.analyze_single("1w", spread_pct=0.001)
        assert row.required_accuracy == pytest.approx(0.64, abs=0.03)
        assert row.assessment == "marginal"

    def test_2week_marginal(self) -> None:
        """2 周周期：所需准确率 p* ≈ 60%，判定为 marginal。"""
        analyzer = FeasibilityAnalyzer()
        row = analyzer.analyze_single("2w", spread_pct=0.001)
        assert row.required_accuracy == pytest.approx(0.60, abs=0.03)
        assert row.assessment == "marginal"

    def test_1month_possible(self) -> None:
        """1 个月周期：所需准确率 p* ≈ 57%，判定为 marginal 或 possible。"""
        analyzer = FeasibilityAnalyzer()
        row = analyzer.analyze_single("1m", spread_pct=0.001)
        assert row.required_accuracy == pytest.approx(0.57, abs=0.03)
        assert row.assessment in ("marginal", "possible")


class TestFeasibilityReport:
    """报告生成与判据校验测试。"""

    def test_full_analysis_runs(self) -> None:
        """全量矩阵分析生成 7 周期 × 4 点差情景 = 28 行测算数据。"""
        analyzer = FeasibilityAnalyzer()
        report = analyzer.analyze()
        assert len(report.rows) == 7 * 4

    def test_has_viable_period(self) -> None:
        """在标准参数设定下，应存在具备统计检验可行性的周期。"""
        analyzer = FeasibilityAnalyzer()
        report = analyzer.analyze()
        assert report.has_viable_period() is True

    def test_no_viable_with_huge_spread(self) -> None:
        """在极端恶劣的点差假定下，所有周期均不可行。"""
        analyzer = FeasibilityAnalyzer()
        report = analyzer.analyze(spread_scenarios=(0.05,))
        assert report.has_viable_period() is False

    def test_render_table_produces_markdown(self) -> None:
        """渲染输出格式为合法的 Markdown 表格。"""
        analyzer = FeasibilityAnalyzer()
        report = analyzer.analyze(
            holding_periods=("1w",),
            spread_scenarios=(0.001,),
        )
        table = report.render_table()
        assert "| 1w |" in table
        assert "---" in table

    def test_report_metadata(self) -> None:
        """报告中正确保留了冻结的参数元数据。"""
        analyzer = FeasibilityAnalyzer()
        report = analyzer.analyze(annual_volatility=0.20)
        assert report.annual_volatility == 0.20
        assert report.sell_fee_rate == 0.004
        assert report.buy_fee_rate == 0.0


class TestHoldingPeriodHours:
    """周期与有效交易小时数映射测试。"""

    def test_1week_is_113_5(self) -> None:
        assert HOLDING_PERIOD_TRADING_HOURS["1w"] == 113.5

    def test_1month_is_4_weeks(self) -> None:
        assert HOLDING_PERIOD_TRADING_HOURS["1m"] == 454.0

    def test_all_periods_defined(self) -> None:
        expected = {"30min", "1h", "4h", "1d", "3d", "1w", "2w", "1m"}
        assert set(HOLDING_PERIOD_TRADING_HOURS.keys()) == expected


class TestLowerSpreadViability:
    """点差与可行性单调关系测试。"""

    def test_zero_spread_weekly_better(self) -> None:
        """零点差情景下所需准确率严于有点差情景。"""
        analyzer = FeasibilityAnalyzer()
        row_zero = analyzer.analyze_single("1w", spread_pct=0.0)
        row_spread = analyzer.analyze_single("1w", spread_pct=0.001)
        assert row_zero.required_accuracy < row_spread.required_accuracy

    def test_cost_monotonicity(self) -> None:
        """同周期下，点差越大所需准确率越高。"""
        analyzer = FeasibilityAnalyzer()
        spreads = [0.0, 0.001, 0.002, 0.003]
        p_stars = [analyzer.analyze_single("1w", s).required_accuracy for s in spreads]
        for i in range(len(p_stars) - 1):
            assert p_stars[i] < p_stars[i + 1]
