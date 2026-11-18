"""基准策略回测与对比分析服务。

实现买入持有（Buy-and-Hold）、定期定额投资（DCA）及持有现金基准。
用于验证量化策略相对被动投资基准是否存在超额增量（Alpha）。
"""

import math
import uuid
from datetime import UTC, datetime

from src.types.domain.benchmark import BaselineComparisonReport, BaselineMetrics
from src.types.domain.product import FeeRule
from src.types.domain.quote import QuoteRaw
from src.types.enums.trading import DataQualityState


class BaselineEngine:
    """被动基线投资策略评测引擎。"""

    def __init__(
        self,
        initial_cash: float = 100000.0,
        fee_rule: FeeRule | None = None,
        annual_risk_free_rate: float = 0.02,
    ) -> None:
        self._initial_cash = initial_cash
        self._fee_rule = fee_rule or FeeRule()
        self._rf_rate = annual_risk_free_rate

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _valid_quotes(self, quotes: list[QuoteRaw]) -> list[QuoteRaw]:
        return sorted(
            (
                q
                for q in quotes
                if q.is_tradable
                and q.quality_state == DataQualityState.NORMAL
                and math.isfinite(q.bid_price)
                and math.isfinite(q.ask_price)
                and 0 < q.bid_price <= q.ask_price
            ),
            key=lambda q: q.available_at or q.received_at,
        )

    def _compute_max_drawdown(self, equity_curve: list[float]) -> float:
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            elif peak > 0:
                dd = (peak - eq) / peak
                if dd > max_dd:
                    max_dd = dd
        return round(max_dd, 6)

    def _compute_sharpe(
        self,
        equity_curve: list[float],
        quotes: list[QuoteRaw],
    ) -> float | None:
        if len(equity_curve) < 2:
            return None
        returns: list[float] = []
        intervals: list[float] = []
        for i in range(1, len(equity_curve)):
            interval = (
                (quotes[i].available_at or quotes[i].received_at)
                - (quotes[i - 1].available_at or quotes[i - 1].received_at)
            ).total_seconds()
            if interval <= 0:
                continue
            intervals.append(interval)
            prev = equity_curve[i - 1]
            if prev > 0:
                returns.append((equity_curve[i] - prev) / prev)
            else:
                returns.append(0.0)

        if not returns:
            return None

        # 不等间隔以有效平均采样间隔近似年化，闭市时间计入实际经过时间。
        annual_periods = 365.0 * 86400.0 / (sum(intervals) / len(intervals))
        mean_ret = sum(returns) / len(returns)
        var = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        std_ret = math.sqrt(var)
        if std_ret < 1e-8:
            return 0.0

        rf_per_period = self._rf_rate / annual_periods
        sharpe = (mean_ret - rf_per_period) / std_ret * math.sqrt(annual_periods)
        return round(sharpe, 4)

    def run_cash_only(
        self,
        quotes: list[QuoteRaw],
    ) -> BaselineMetrics:
        """运行纯持有现金（不交易）基准。"""
        return BaselineMetrics(
            strategy_name="CASH_ONLY",
            total_return_pct=0.0,
            annualized_return_pct=0.0,
            max_drawdown_pct=0.0,
            sharpe_ratio=0.0,
            total_trades=0,
            total_fee_cny=0.0,
            final_equity=self._initial_cash,
        )

    def run_buy_and_hold(
        self,
        quotes: list[QuoteRaw],
    ) -> BaselineMetrics:
        """运行买入持有（一次性全仓）基准。"""
        tradable_quotes = self._valid_quotes(quotes)
        if not tradable_quotes:
            return self.run_cash_only(quotes)

        # 首个可用报价全仓买入
        first_quote = tradable_quotes[0]
        buy_price = first_quote.ask_price
        buy_fee_rate = self._fee_rule.buy_fee_rate
        sell_fee_rate = self._fee_rule.sell_fee_rate

        # 可买克数 = initial_cash / (buy_price * (1 + buy_fee_rate))
        cost_per_gram = buy_price * (1.0 + buy_fee_rate)
        grams = self._initial_cash / cost_per_gram
        initial_fee = grams * buy_price * buy_fee_rate

        equity_curve: list[float] = [self._initial_cash]
        for q in tradable_quotes:
            # 净值 = 变现后净残值
            est_value = grams * q.bid_price * (1.0 - sell_fee_rate)
            equity_curve.append(est_value)

        last_quote = tradable_quotes[-1]
        final_sell_fee = grams * last_quote.bid_price * sell_fee_rate
        final_equity = grams * last_quote.bid_price - final_sell_fee
        total_fee = initial_fee + final_sell_fee

        total_return = (final_equity - self._initial_cash) / self._initial_cash
        max_dd = self._compute_max_drawdown(equity_curve)

        # 计算年化
        duration_days = max(
            (
                (last_quote.available_at or last_quote.received_at)
                - (first_quote.available_at or first_quote.received_at)
            ).total_seconds()
            / 86400.0,
            1.0,
        )
        annualized = (1.0 + total_return) ** (365.0 / duration_days) - 1.0

        sharpe = self._compute_sharpe(equity_curve[1:], tradable_quotes)

        return BaselineMetrics(
            strategy_name="BUY_AND_HOLD",
            total_return_pct=round(total_return, 6),
            annualized_return_pct=round(annualized, 6),
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
            total_trades=2,  # 一买一卖
            total_fee_cny=round(total_fee, 2),
            final_equity=round(final_equity, 2),
        )

    def run_dca(
        self,
        quotes: list[QuoteRaw],
        step_intervals: int = 5,
    ) -> BaselineMetrics:
        """运行定期定额定投（DCA）基准。"""
        if step_intervals <= 0:
            raise ValueError("定投采样间隔必须为正整数")
        tradable_quotes = self._valid_quotes(quotes)
        if not tradable_quotes:
            return self.run_cash_only(quotes)

        # 规划定投批次
        total_steps = max(len(tradable_quotes) // step_intervals, 1)
        cash_per_step = self._initial_cash / total_steps
        buy_fee_rate = self._fee_rule.buy_fee_rate
        sell_fee_rate = self._fee_rule.sell_fee_rate

        remaining_cash = self._initial_cash
        accumulated_grams = 0.0
        total_fees = 0.0
        trades_count = 0
        equity_curve: list[float] = [self._initial_cash]

        for idx, q in enumerate(tradable_quotes):
            # 每隔 step_intervals 步买入一次
            if idx % step_intervals == 0 and remaining_cash >= cash_per_step:
                cost_per_g = q.ask_price * (1.0 + buy_fee_rate)
                bought_g = cash_per_step / cost_per_g
                fee = bought_g * q.ask_price * buy_fee_rate
                remaining_cash -= cash_per_step
                accumulated_grams += bought_g
                total_fees += fee
                trades_count += 1

            # 计算当前时点总资产净值
            est_gold_value = accumulated_grams * q.bid_price * (1.0 - sell_fee_rate)
            current_total_equity = remaining_cash + est_gold_value
            equity_curve.append(current_total_equity)

        # 期末清算净值
        last_quote = tradable_quotes[-1]
        final_sell_fee = accumulated_grams * last_quote.bid_price * sell_fee_rate
        final_gold_cny = accumulated_grams * last_quote.bid_price - final_sell_fee
        final_equity = remaining_cash + final_gold_cny
        total_fees += final_sell_fee
        if accumulated_grams > 0:
            trades_count += 1

        total_return = (final_equity - self._initial_cash) / self._initial_cash
        max_dd = self._compute_max_drawdown(equity_curve)

        duration_days = max(
            (
                (last_quote.available_at or last_quote.received_at)
                - (tradable_quotes[0].available_at or tradable_quotes[0].received_at)
            ).total_seconds()
            / 86400.0,
            1.0,
        )
        annualized = (1.0 + total_return) ** (365.0 / duration_days) - 1.0
        sharpe = self._compute_sharpe(equity_curve[1:], tradable_quotes)

        return BaselineMetrics(
            strategy_name="DCA",
            total_return_pct=round(total_return, 6),
            annualized_return_pct=round(annualized, 6),
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
            total_trades=trades_count,
            total_fee_cny=round(total_fees, 2),
            final_equity=round(final_equity, 2),
        )

    def compare_all(
        self,
        quotes: list[QuoteRaw],
        dca_step_interval: int = 5,
    ) -> BaselineComparisonReport:
        """运行全部基准策略并生成横向对比报告。"""
        b_cash = self.run_cash_only(quotes)
        b_bh = self.run_buy_and_hold(quotes)
        b_dca = self.run_dca(quotes, step_intervals=dca_step_interval)

        results = [b_cash, b_bh, b_dca]
        best_strat = max(results, key=lambda m: m.total_return_pct)

        summary = (
            f"基准策略评测完成：最优策略为 {best_strat.strategy_name}，"
            f"总收益率 {best_strat.total_return_pct * 100:.2f}%，"
            f"最大回撤 {best_strat.max_drawdown_pct * 100:.2f}%"
        )

        return BaselineComparisonReport(
            report_id=f"base_rep_{uuid.uuid4().hex[:8]}",
            as_of=self._now(),
            metrics=results,
            best_strategy=best_strat.strategy_name,
            summary=summary,
        )
