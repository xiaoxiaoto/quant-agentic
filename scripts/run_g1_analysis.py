"""经济可行性分析计算与报告生成脚本。

运行方式：
    uv run python scripts/run_g1_analysis.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.decision.feasibility import FeasibilityAnalyzer


def main() -> None:
    analyzer = FeasibilityAnalyzer()
    report = analyzer.analyze(
        annual_volatility=0.17,
        spread_scenarios=(0.0005, 0.0010, 0.0020, 0.0030),
    )

    print("=" * 80)
    print("黄金积存 经济可行性分析测算输出")
    print(f"年化波动率 σ = {report.annual_volatility:.1%}")
    print(f"年交易小时数 = {report.trading_hours_per_year:.1f} h")
    print(f"买入手续费 = {report.buy_fee_rate:.2%}, 卖出手续费 = {report.sell_fee_rate:.2%}")
    print("=" * 80)
    print()
    print(report.render_table())
    print()
    print("=" * 80)
    if report.has_viable_period():
        print("【结论】存在潜在可行持有期（2w / 1m 边缘或可能，要求准确率约 56%~59%）。")
        print("【警告】分钟级至日级由于 0.4% 卖出费与点差磨损，所需方向准确率 >75%，无正期望空间。")
    else:
        print("【结论】可行性评估未通过，无可行持有期，系统应降级为提醒工具或终止。")
    print("=" * 80)


if __name__ == "__main__":
    main()
