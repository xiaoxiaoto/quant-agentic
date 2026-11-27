"""Qlib 单标的（Au99.99）滚动样本外阈值择时回测服务。"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta
from pathlib import Path

from src.contracts.domains.qlib import (
    QlibBacktestEquityPoint,
    QlibBacktestSummary,
    QlibBacktestTrade,
)
from src.contracts.exceptions import QlibDatasetError
from src.contracts.schemas.qlib import QlibBacktestReport, QlibBacktestRequest
from src.services.qlib.research_service import QlibResearchService


class QlibBacktestService:
    """消费严格样本外滚动预测序列，模拟黄金现货单标的做多/空仓阈值择时回测。"""

    def __init__(self, research: QlibResearchService) -> None:
        self._research = research

    @staticmethod
    def _annual_factor(freq: str) -> float:
        return 252.0 if freq == "day" else 252.0 * 24.0

    @classmethod
    def _compute_summary(
        cls,
        *,
        initial_cash: float,
        equity_curve: list[QlibBacktestEquityPoint],
        trades: list[QlibBacktestTrade],
        round_trip_pnls: list[float],
        total_fees: float,
        freq: str,
    ) -> QlibBacktestSummary:
        final_equity = equity_curve[-1].strategy_equity if equity_curve else initial_cash
        total_return = (final_equity / initial_cash) - 1.0 if initial_cash > 0 else 0.0
        bench_final = equity_curve[-1].benchmark_equity if equity_curve else initial_cash
        benchmark_return = (bench_final / initial_cash) - 1.0 if initial_cash > 0 else 0.0
        excess_return = total_return - benchmark_return

        n_bars = len(equity_curve)
        ann_factor = cls._annual_factor(freq)
        if n_bars >= 2 and final_equity > 0:
            years = max(n_bars / ann_factor, 1.0 / ann_factor)
            annual_return = (final_equity / initial_cash) ** (1.0 / years) - 1.0
        else:
            annual_return = total_return

        bar_returns: list[float] = []
        peak = initial_cash
        max_drawdown = 0.0
        prev_eq = initial_cash
        for point in equity_curve:
            eq = point.strategy_equity
            if eq > peak:
                peak = eq
            if peak > 0:
                dd = (peak - eq) / peak
                if dd > max_drawdown:
                    max_drawdown = dd
            if prev_eq > 0:
                bar_returns.append((eq / prev_eq) - 1.0)
            prev_eq = eq

        sharpe = 0.0
        if len(bar_returns) >= 2:
            ret_std = statistics.stdev(bar_returns)
            if ret_std > 1e-12:
                sharpe = (statistics.mean(bar_returns) / ret_std) * math.sqrt(ann_factor)

        calmar = (annual_return / max_drawdown) if max_drawdown > 1e-12 else 0.0
        wins = sum(1 for pnl in round_trip_pnls if pnl > 0)
        win_rate = (wins / len(round_trip_pnls)) if round_trip_pnls else 0.0

        return QlibBacktestSummary(
            initial_cash=round(initial_cash, 2),
            final_equity=round(final_equity, 2),
            total_return=round(total_return, 6),
            annual_return=round(annual_return, 6),
            sharpe_ratio=round(sharpe, 4),
            max_drawdown=round(max_drawdown, 6),
            calmar_ratio=round(calmar, 4),
            benchmark_return=round(benchmark_return, 6),
            excess_return=round(excess_return, 6),
            trade_count=len(trades),
            round_trip_count=len(round_trip_pnls),
            win_rate=round(win_rate, 4),
            total_fees=round(total_fees, 4),
            bars_evaluated=n_bars,
        )

    @classmethod
    def simulate_timing(
        cls,
        *,
        oos_predictions: list[tuple[datetime, float, float, float, float]],
        closes_by_dt: dict[datetime, float],
        request: QlibBacktestRequest,
    ) -> tuple[QlibBacktestSummary, list[QlibBacktestTrade], list[QlibBacktestEquityPoint]]:
        """根据样本外预测得分与收盘价序列执行单标的阈值择时仿真（做多/空仓）。"""
        aligned: list[tuple[datetime, float, float, float]] = []
        for dt, score, _prob, lcb, _actual in oos_predictions:
            price = closes_by_dt.get(dt)
            if price is None or not math.isfinite(price) or price <= 0:
                continue
            aligned.append((dt, score, lcb, price))
        if not aligned:
            raise QlibDatasetError("样本外预测与收盘价无有效对齐区间，无法执行择时回测")

        cash = request.initial_cash
        position_grams = 0.0
        entry_cost_total = 0.0
        total_fees = 0.0
        trades: list[QlibBacktestTrade] = []
        round_trip_pnls: list[float] = []
        equity_curve: list[QlibBacktestEquityPoint] = []

        valid_closes = sorted((dt, p) for dt, p in closes_by_dt.items() if math.isfinite(p) and p > 0)
        prev_close_by_dt: dict[datetime, tuple[datetime, float]] = {}
        for idx in range(1, len(valid_closes)):
            prev_dt, prev_val = valid_closes[idx - 1]
            curr_dt, _ = valid_closes[idx]
            prev_close_by_dt[curr_dt] = (prev_dt, prev_val)
        max_gap = timedelta(days=10) if request.freq == "day" else timedelta(hours=6)

        first_price = aligned[0][3]
        benchmark_grams = (request.initial_cash / (first_price * (1.0 + request.open_cost))) if first_price > 0 else 0.0

        for dt, score, lcb, price in aligned:
            prev_entry = prev_close_by_dt.get(dt)
            if prev_entry is not None and prev_entry[1] > 0 and (dt - prev_entry[0]) <= max_gap:
                daily_ret = (price / prev_entry[1]) - 1.0
                limit_up = request.limit_threshold is not None and daily_ret >= request.limit_threshold
                limit_down = request.limit_threshold is not None and daily_ret <= -request.limit_threshold
            else:
                limit_up = False
                limit_down = False

            buy_allowed = score > request.buy_thresh and (not request.use_lcb_gate or lcb > 0.0)
            sell_allowed = score < request.sell_thresh

            if position_grams == 0.0 and buy_allowed and not limit_up:
                budget = cash * request.cash_utilization
                unit_cost = price * (1.0 + request.open_cost) * request.trade_unit_grams
                lots = int(budget // unit_cost) if unit_cost > 0 else 0
                grams = lots * request.trade_unit_grams
                if grams > 0:
                    gross = grams * price
                    fee = gross * request.open_cost
                    total_out = gross + fee
                    if total_out <= cash + 1e-9:
                        cash = max(0.0, cash - total_out)
                        position_grams = grams
                        entry_cost_total = total_out
                        total_fees += fee
                        equity_after = cash + position_grams * price
                        trades.append(
                            QlibBacktestTrade(
                                timestamp=dt,
                                action="BUY",
                                price=round(price, 4),
                                grams=round(grams, 4),
                                fee=round(fee, 4),
                                score=round(score, 6),
                                net_return_lcb=round(lcb, 6),
                                cash_after=round(cash, 2),
                                position_grams_after=round(position_grams, 4),
                                equity_after=round(equity_after, 2),
                            )
                        )
            elif position_grams > 0.0 and sell_allowed and not limit_down:
                grams = position_grams
                gross = grams * price
                fee = gross * request.close_cost
                net_in = gross - fee
                cash += net_in
                position_grams = 0.0
                total_fees += fee
                round_trip_pnls.append(net_in - entry_cost_total)
                entry_cost_total = 0.0
                trades.append(
                    QlibBacktestTrade(
                        timestamp=dt,
                        action="SELL",
                        price=round(price, 4),
                        grams=round(grams, 4),
                        fee=round(fee, 4),
                        score=round(score, 6),
                        net_return_lcb=round(lcb, 6),
                        cash_after=round(cash, 2),
                        position_grams_after=0.0,
                        equity_after=round(cash, 2),
                    )
                )

            strategy_equity = cash + position_grams * price
            benchmark_equity = benchmark_grams * price
            equity_curve.append(
                QlibBacktestEquityPoint(
                    timestamp=dt,
                    close_price=round(price, 4),
                    score=round(score, 6),
                    position_grams=round(position_grams, 4),
                    strategy_equity=round(strategy_equity, 2),
                    benchmark_equity=round(benchmark_equity, 2),
                )
            )

        summary = cls._compute_summary(
            initial_cash=request.initial_cash,
            equity_curve=equity_curve,
            trades=trades,
            round_trip_pnls=round_trip_pnls,
            total_fees=total_fees,
            freq=request.freq,
        )
        return summary, trades, equity_curve

    def run_backtest(
        self,
        request: QlibBacktestRequest,
        *,
        provider_uri_override: Path | None = None,
    ) -> QlibBacktestReport:
        """执行单标的滚动样本外训练与阈值择时回测。"""
        provider_uri, folds, oos_preds, closes_by_dt = self._research.run_rolling_predictions(
            source_type=request.source_type,
            provider_uri_override=provider_uri_override,
            provider_uri_raw=request.provider_uri,
            instrument=request.instrument,
            freq=request.freq,
            horizon=request.horizon,
            factor_preset=request.factor_preset,
            label_mode=request.label_mode,
            custom_expressions=request.custom_expressions,
            model_type=request.model_type,
            window_mode=request.window_mode,
            train_window_bars=request.train_window_bars,
            step_bars=request.step_bars,
            use_robust_zscore=request.use_robust_zscore,
            n_splits=request.n_splits,
            num_boost_round=request.num_boost_round,
            lgb_max_depth=request.lgb_max_depth,
            lgb_num_leaves=request.lgb_num_leaves,
            lgb_reg_alpha=request.lgb_reg_alpha,
            lgb_reg_lambda=request.lgb_reg_lambda,
            confidence_level=request.confidence_level,
            spread_pct=request.spread_pct,
            start_time=request.start_time,
            end_time=request.end_time,
        )
        if not oos_preds:
            raise QlibDatasetError(f"品种 {request.instrument} 样本数不足，未能生成有效滚动样本外预测")
        summary, trades, equity_curve = self.simulate_timing(
            oos_predictions=oos_preds,
            closes_by_dt=closes_by_dt,
            request=request,
        )
        return QlibBacktestReport(
            instrument=request.instrument,
            provider_uri=str(provider_uri),
            freq=request.freq,
            horizon=request.horizon,
            factor_preset=request.factor_preset,
            window_mode=request.window_mode,
            summary=summary,
            walk_forward_folds=folds,
            trades=trades,
            equity_curve=equity_curve,
        )
