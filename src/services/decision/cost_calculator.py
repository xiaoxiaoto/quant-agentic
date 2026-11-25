"""成本与盈亏平衡计算器。

所有成本核算必须基于银行可成交价格口径。
银行双边报价的点差已内嵌在买卖价中，严禁重复扣除点差。

关键恒等式（零点差、零买入手续费基准下）：
    盈亏平衡点 = 1 / (1 − sell_fee_rate) − 1 ≈ 0.4016%

包含实测点差 s 时：
    盈亏平衡点 ≈ 0.4016% + s
"""

import math

from src.contracts.domains.product import FeeRule


class CostCalculator:
    """积存黄金成本、盈亏与盈亏平衡计算器。

    支持注入带版本的 FeeRule，以匹配不同历史时期的实际费率环境。
    """

    def __init__(self, fee_rule: FeeRule | None = None) -> None:
        self._fee = fee_rule or FeeRule()

    # ── 开仓与平仓现金流 ──────────────────────────────────────────

    def entry_cash_out(self, quantity_g: float, ask_price: float) -> float:
        """以 ask_price 买入 quantity_g 克的现金总流出。

        entry_cash_out = q × P_ask × (1 + buy_fee_rate)
        """
        return quantity_g * ask_price * (1.0 + self._fee.buy_fee_rate)

    def exit_cash_in(self, quantity_g: float, bid_price: float) -> float:
        """以 bid_price 卖出 quantity_g 克的现金净流入。

        exit_cash_in = q × P_bid × (1 − sell_fee_rate)
        """
        return quantity_g * bid_price * (1.0 - self._fee.sell_fee_rate)

    def net_pnl(
        self,
        quantity_g: float,
        entry_ask: float,
        exit_bid: float,
        other_costs: float = 0.0,
    ) -> float:
        """单笔往返交易的绝对净盈亏金额。

        net_pnl = exit_cash_in − entry_cash_out − verified_other_costs
        """
        return self.exit_cash_in(quantity_g, exit_bid) - self.entry_cash_out(quantity_g, entry_ask) - other_costs

    def net_return(
        self,
        entry_ask: float,
        exit_bid: float,
    ) -> float:
        """单笔往返交易的净收益率（基于初始投入本金）。"""
        entry = entry_ask * (1.0 + self._fee.buy_fee_rate)
        exit_ = exit_bid * (1.0 - self._fee.sell_fee_rate)
        return exit_ / entry - 1.0

    # ── 盈亏平衡指标 ──────────────────────────────────────────────

    def break_even_bid(self, ask_price: float) -> float:
        """买入成交价为 ask_price 时，实现保本所需的最低卖出价（bid）。

        break_even_bid = P_ask × (1 + buy_fee_rate) / (1 − sell_fee_rate)
        """
        return ask_price * (1.0 + self._fee.buy_fee_rate) / (1.0 - self._fee.sell_fee_rate)

    def break_even_pct(self, spread_pct: float = 0.0) -> float:
        """实现盈亏平衡所需的最小涨幅。

        无点差下界：1 / (1 − f_sell) − 1 ≈ 0.4016%（当卖出费为 0.4% 时）。
        含实测点差 s 时：约为 0.4016% + s。
        """
        base = (1.0 + self._fee.buy_fee_rate) / (1.0 - self._fee.sell_fee_rate) - 1.0
        return base + spread_pct

    # ── 净退出损失与风控口径 ──────────────────────────────────────

    def net_exit_loss_per_gram(
        self,
        ask_price: float,
        stop_distance_pct: float,
        spread_pct: float,
    ) -> float:
        """止损触发时每克黄金的净退出损失金额。

        L_g ≈ P_ask × (d + s + f_buy + f_sell)
        其中 d 为止损距离比例，s 为点差比例，f_buy / f_sell 为买卖费率。
        """
        return ask_price * (stop_distance_pct + spread_pct + self._fee.buy_fee_rate + self._fee.sell_fee_rate)

    def total_round_trip_cost_pct(self, spread_pct: float = 0.0) -> float:
        """往返总成本比例（手续费 + 点差）。

        C = buy_fee_rate + sell_fee_rate + spread
        """
        return self._fee.buy_fee_rate + self._fee.sell_fee_rate + spread_pct

    # ── 经济可行性测算公式 ────────────────────────────────────────

    def required_accuracy(self, total_cost_pct: float, typical_move_pct: float) -> float:
        """获得非负期望净收益所需的最低方向准确率下界。

        p* = 0.5 + C / (2m)
        其中 C 为往返总成本比例，m 为该持有期内的典型绝对价格变动。

        当总成本大于 2m 时返回 > 1.0（数学上不可能获利）。
        """
        if typical_move_pct <= 0:
            return math.inf
        return 0.5 + total_cost_pct / (2.0 * typical_move_pct)

    @staticmethod
    def typical_absolute_move(
        annual_volatility: float,
        holding_hours: float,
        trading_hours_per_year: float = 6318.0,
    ) -> float:
        """持有期内价格绝对变动的典型值估算。

        m ≈ 0.8 × σ_annual × √(h / H_year)

        0.8 的系数用于将标准差折算为平均绝对离差（正态分布 MAD ≈ 0.7979σ）。
        """
        sigma_h = annual_volatility * math.sqrt(holding_hours / trading_hours_per_year)
        return 0.8 * sigma_h

    @staticmethod
    def intl_reference_cny_per_gram(xau_usd: float, usd_cnh: float) -> float:
        """国际金价折算为人民币元/克的价格。

        P_intl = XAUUSD × USDCNH / 31.1034768
        """
        return xau_usd * usd_cnh / 31.1034768
