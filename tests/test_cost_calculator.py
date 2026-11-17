"""成本与盈亏平衡计算器单元测试。"""

import math

import pytest

from src.services.cost_calculator import CostCalculator
from src.types.domain.product import FeeRule


class TestBreakEven:
    """盈亏平衡计算测试——复核关键量级不变式。"""

    def test_break_even_pct_no_spread(self) -> None:
        """零点差时：1/(1−0.004) − 1 ≈ 0.4016% 保本涨幅。"""
        calc = CostCalculator()
        be = calc.break_even_pct(spread_pct=0.0)
        assert be == pytest.approx(0.004016, rel=1e-3)

    def test_break_even_pct_with_spread(self) -> None:
        """含点差 s=0.1% 时：盈亏平衡涨幅 ≈ 0.4016% + 0.1% ≈ 0.50%。"""
        calc = CostCalculator()
        be = calc.break_even_pct(spread_pct=0.001)
        assert be == pytest.approx(0.005016, rel=1e-3)

    def test_break_even_bid_at_1000(self) -> None:
        """以 1000 元/克买入，保本卖出价需 ≥ 约 1004.016 元/克。"""
        calc = CostCalculator()
        be_bid = calc.break_even_bid(ask_price=1000.0)
        assert be_bid == pytest.approx(1004.016, rel=1e-4)


class TestNetPnl:
    """往返净盈亏与现金流计算测试。"""

    def test_round_trip_at_break_even(self) -> None:
        """在盈亏平衡卖出价平仓时，净盈亏应当近似为 0。"""
        calc = CostCalculator()
        be_bid = calc.break_even_bid(ask_price=1000.0)
        pnl = calc.net_pnl(quantity_g=1.0, entry_ask=1000.0, exit_bid=be_bid)
        assert pnl == pytest.approx(0.0, abs=0.01)

    def test_round_trip_losing_trade(self) -> None:
        """卖出价未覆盖手续费时产生净亏损。"""
        calc = CostCalculator()
        pnl = calc.net_pnl(quantity_g=1.0, entry_ask=1000.0, exit_bid=1000.0)
        # 扣除 0.4% 卖出手续费：1000 × (1 − 0.004) − 1000 = −4.0
        assert pnl == pytest.approx(-4.0, abs=0.01)

    def test_net_return_at_break_even(self) -> None:
        """盈亏平衡价处的净收益率应当为 0。"""
        calc = CostCalculator()
        be_bid = calc.break_even_bid(ask_price=1000.0)
        ret = calc.net_return(entry_ask=1000.0, exit_bid=be_bid)
        assert ret == pytest.approx(0.0, abs=1e-6)

    def test_entry_cash_out_no_buy_fee(self) -> None:
        """零买入费率下，买入现金流出 = 克数 × 买入价。"""
        calc = CostCalculator()
        assert calc.entry_cash_out(1.0, 1000.0) == pytest.approx(1000.0)

    def test_exit_cash_in(self) -> None:
        """卖出现金流入 = 克数 × 卖出价 × (1 − 0.004)。"""
        calc = CostCalculator()
        result = calc.exit_cash_in(1.0, 1000.0)
        assert result == pytest.approx(996.0)


class TestNetExitLoss:
    """净退出损失测试。"""

    def test_net_exit_loss_example(self) -> None:
        """止损 d=0.4%, 点差 s=0.1%, 费率 f=0.4% → 约 0.9%。"""
        calc = CostCalculator()
        loss = calc.net_exit_loss_per_gram(
            ask_price=1000.0,
            stop_distance_pct=0.004,
            spread_pct=0.001,
        )
        expected = 1000.0 * (0.004 + 0.001 + 0.004)
        assert loss == pytest.approx(expected)
        assert loss == pytest.approx(9.0)  # 1000 元的 0.9%


class TestRequiredAccuracy:
    """所需方向准确率 p* = 0.5 + C/(2m) 测试。"""

    def test_impossible_30min(self) -> None:
        """30 分钟窗口：成本 0.5% 下所需准确率 > 100%（不可能）。"""
        m = CostCalculator.typical_absolute_move(0.17, 0.5, 6318.0)
        calc = CostCalculator()
        p_star = calc.required_accuracy(total_cost_pct=0.005, typical_move_pct=m)
        assert p_star > 1.0

    def test_weekly_about_63pct(self) -> None:
        """1 周周期：成本 0.5% 下所需准确率约为 63%。"""
        m = CostCalculator.typical_absolute_move(0.17, 113.5, 6318.0)
        calc = CostCalculator()
        p_star = calc.required_accuracy(total_cost_pct=0.005, typical_move_pct=m)
        assert p_star == pytest.approx(0.63, abs=0.03)

    def test_zero_move_returns_inf(self) -> None:
        """变动为 0 时所需准确率为正无穷。"""
        calc = CostCalculator()
        assert calc.required_accuracy(0.005, 0.0) == math.inf


class TestTypicalMove:
    """典型绝对变动 m ≈ 0.8 × σ_annual × √(h / H_year) 测试。"""

    def test_30min_move(self) -> None:
        """30 分钟：典型变动约为 0.12%。"""
        m = CostCalculator.typical_absolute_move(0.17, 0.5, 6318.0)
        assert m == pytest.approx(0.0012, abs=0.0002)

    def test_daily_move(self) -> None:
        """1 日（~23h）：典型变动约为 0.81%。"""
        m = CostCalculator.typical_absolute_move(0.17, 22.7, 6318.0)
        assert m == pytest.approx(0.0081, abs=0.001)

    def test_weekly_move(self) -> None:
        """1 周（113.5h）：典型变动约为 1.82%。"""
        m = CostCalculator.typical_absolute_move(0.17, 113.5, 6318.0)
        assert m == pytest.approx(0.0182, abs=0.002)


class TestIntlReference:
    """国际金价折算元/克测试。"""

    def test_xauusd_conversion(self) -> None:
        """P_intl = XAUUSD × USDCNH / 31.1034768。"""
        p = CostCalculator.intl_reference_cny_per_gram(xau_usd=2000.0, usd_cnh=7.2)
        expected = 2000.0 * 7.2 / 31.1034768
        assert p == pytest.approx(expected)


class TestCustomFeeRule:
    """支持自定义/带版本费率规则测试。"""

    def test_with_buy_fee(self) -> None:
        """当买入费率非零时，盈亏平衡涨幅要求相应提高。"""
        fee = FeeRule(buy_fee_rate=0.001, sell_fee_rate=0.004)
        calc = CostCalculator(fee)
        be_zero = CostCalculator().break_even_pct()
        be_with = calc.break_even_pct()
        assert be_with > be_zero
