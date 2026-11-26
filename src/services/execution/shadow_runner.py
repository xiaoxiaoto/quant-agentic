"""影子运行与消融验证仿真服务。

驱动行情流式回放，串联特征工程、模型预测、元决策、独立风控与 RFQ 成交模型。
核算净值曲线、最大回撤、交易成本与夏普比率。
"""

import math
import uuid
from datetime import UTC, datetime

from src.contracts.domains.benchmark import ShadowRunReport
from src.contracts.domains.execution import RFQRequest
from src.contracts.domains.feature import ModelPrediction
from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState, DecisionAction, HoldingPeriod, RiskState, TradeDirection
from src.services.decision.cost_calculator import CostCalculator
from src.services.decision.decision_engine import DecisionEngine
from src.services.execution.rfq_execution import RFQExecutionEngine
from src.services.execution.shadow_ledger import ShadowLedger
from src.services.prediction.feature_engine import FeatureEngine, ModelContextUnavailableError
from src.services.prediction.predictor import ModelPredictor
from src.services.risk.risk_engine import RiskEngine


class ShadowRunner:
    """全链路影子运行仿真引擎。"""

    def __init__(
        self,
        initial_cash: float = 100000.0,
        feature_engine: FeatureEngine | None = None,
        predictor: ModelPredictor | None = None,
        decision_engine: DecisionEngine | None = None,
        risk_engine: RiskEngine | None = None,
        rfq_engine: RFQExecutionEngine | None = None,
        fee_rule: FeeRule | None = None,
    ) -> None:
        if not math.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("初始资金必须为正且有限")
        self._initial_cash = initial_cash
        self._fee_rule = fee_rule or FeeRule()
        self._calculator = CostCalculator(self._fee_rule)
        self._features = feature_engine or FeatureEngine()
        self._predictor = predictor or ModelPredictor(fee_rule=self._fee_rule)
        self._decision = decision_engine or DecisionEngine(fee_rule=self._fee_rule)
        self._risk = risk_engine or RiskEngine(fee_rule=self._fee_rule)
        self._rfq = rfq_engine or RFQExecutionEngine(fee_rule=self._fee_rule)

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def run_shadow_simulation(
        self,
        quotes: list[QuoteRaw],
        trade_grams_per_order: float = 2.0,
    ) -> ShadowRunReport:
        """在历史连续报价序列上执行全链路影子仿真。"""
        run_id = f"shadow_{uuid.uuid4().hex[:10]}"
        if not quotes:
            now_dt = self._now()
            return ShadowRunReport(
                run_id=run_id,
                start_time=now_dt,
                end_time=now_dt,
                initial_cash=self._initial_cash,
                final_equity=self._initial_cash,
                total_return_pct=0.0,
                annualized_return_pct=0.0,
                max_drawdown_pct=0.0,
            )

        sorted_quotes = sorted(quotes, key=lambda q: max(q.quote_at, q.received_at, q.available_at or q.received_at))
        start_time = max(
            sorted_quotes[0].quote_at,
            sorted_quotes[0].received_at,
            sorted_quotes[0].available_at or sorted_quotes[0].received_at,
        )
        end_time = max(
            sorted_quotes[-1].quote_at,
            sorted_quotes[-1].received_at,
            sorted_quotes[-1].available_at or sorted_quotes[-1].received_at,
        )

        ledger = ShadowLedger(initial_cash=self._initial_cash, fee_rule=self._fee_rule)
        equity_curve: list[float] = []
        trades_count = 0
        winning_trades = 0
        closed_trades = 0
        total_fees = 0.0
        warmup_count = 0
        last_valid_bid: float | None = None
        last_signal_at: datetime | None = None
        risk_state = RiskState.NORMAL

        # 增量特征提取会话，复用双周期公共特征与在线波动率统计，避免 O(n²) 重复计算
        feature_stream = self._features.create_stream() if hasattr(self._features, "create_stream") else None
        history_window: list[QuoteRaw] = []

        for i in range(len(sorted_quotes)):
            curr_q = sorted_quotes[i]
            is_valid_quote = (
                curr_q.quality_state == DataQualityState.NORMAL
                and math.isfinite(curr_q.bid_price)
                and math.isfinite(curr_q.ask_price)
                and 0 < curr_q.bid_price <= curr_q.ask_price
            )
            if is_valid_quote:
                last_valid_bid = curr_q.bid_price
            current_time = max(curr_q.quote_at, curr_q.received_at, curr_q.available_at or curr_q.received_at)

            # 上下文不足只预热并弃权；真实推理失败不得继续生成交易建议。
            preds: dict[HoldingPeriod, ModelPrediction] = {}
            try:
                if feature_stream is not None:
                    feats_by_horizon = feature_stream.push_and_extract(
                        curr_q,
                        as_of=current_time,
                        horizons=(HoldingPeriod.W1, HoldingPeriod.D1),
                    )
                    feats = feats_by_horizon[HoldingPeriod.W1]
                    day_feats = feats_by_horizon[HoldingPeriod.D1]
                else:
                    history_window.append(curr_q)
                    feats = self._features.extract_features(
                        history_window, as_of=current_time, horizon=HoldingPeriod.W1
                    )
                    day_feats = self._features.extract_features(
                        history_window, as_of=current_time, horizon=HoldingPeriod.D1
                    )
            except ModelContextUnavailableError:
                warmup_count += 1
            else:
                preds = {
                    HoldingPeriod.W1: self._predictor.predict(feats, horizon=HoldingPeriod.W1),
                    HoldingPeriod.D1: self._predictor.predict(day_feats, horizon=HoldingPeriod.D1),
                }

            # 3. 元决策引擎评估
            snap = ledger.take_snapshot(current_time)
            dec = self._decision.evaluate_decision(
                quote=curr_q,
                predictions=preds,
                account=snap,
                target_buy_grams=trade_grams_per_order,
                as_of=current_time,
                last_signal_at=last_signal_at,
            )

            # 4. 独立风控一票否决（含日/周回撤状态推进）
            valuation_bid = curr_q.bid_price if is_valid_quote else last_valid_bid
            if valuation_bid is not None:
                daily_pnl, weekly_pnl = ledger.compute_period_pnl(current_time, valuation_bid)
            else:
                daily_pnl, weekly_pnl = None, None
            approved_dec, risk_state, violations = self._risk.check_decision(
                decision=dec,
                account=snap,
                current_ask_price=curr_q.ask_price,
                current_bid_price=curr_q.bid_price,
                risk_state=risk_state,
                daily_pnl=daily_pnl,
                weekly_pnl=weekly_pnl,
            )

            # 5. RFQ 成交模型仿真
            if approved_dec.action in (DecisionAction.BUY, DecisionAction.SELL):
                last_signal_at = current_time
                direction = TradeDirection.BUY if approved_dec.action == DecisionAction.BUY else TradeDirection.SELL
                req = RFQRequest(
                    request_id=f"req_{i}",
                    direction=direction,
                    grams=abs(approved_dec.delta_g),
                    decision_time=current_time,
                    eligible_at=current_time,
                    is_programmatic=True,
                )
                fill = self._rfq.simulate_execution(req, candidate_quotes=[curr_q])
                if fill.is_filled and fill.filled_price is not None:
                    if direction == TradeDirection.BUY:
                        try:
                            ledger.record_buy(
                                client_order_id=f"cid_{i}",
                                grams=fill.filled_grams,
                                price=fill.filled_price,
                                fee=fill.fee_cny,
                                timestamp=fill.filled_at or current_time,
                            )
                        except ValueError:
                            # 滑点导致实际所需资金略超可用现金时按拒单处理，不中断回放
                            pass
                        else:
                            trades_count += 1
                            total_fees += fill.fee_cny
                    else:
                        previous_pnl = ledger.take_snapshot().realized_pnl
                        ledger.record_sell(
                            client_order_id=f"cid_{i}",
                            grams=fill.filled_grams,
                            price=fill.filled_price,
                            fee=fill.fee_cny,
                            timestamp=fill.filled_at or current_time,
                        )
                        trades_count += 1
                        total_fees += fill.fee_cny
                        # 检查已实现盈亏是否为正
                        current_snap = ledger.take_snapshot(current_time)
                        closed_trades += 1
                        if current_snap.realized_pnl > previous_pnl:
                            winning_trades += 1

            # 仅使用有效正常报价更新净值曲线，防止异常报价污染指标或崩溃
            if last_valid_bid is not None:
                ledger.mark_to_market(last_valid_bid)
                snapshot = ledger.take_snapshot(current_time)
                eq = (
                    snapshot.cash_available
                    + snapshot.cash_frozen
                    + self._calculator.exit_cash_in(snapshot.gold_total_g, last_valid_bid)
                )
                equity_curve.append(eq)

        # 结算绩效指标
        final_equity = equity_curve[-1] if equity_curve else self._initial_cash
        total_return = (final_equity - self._initial_cash) / self._initial_cash

        # 计算最大回撤
        peak = self._initial_cash
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            elif peak > 0:
                dd = (peak - eq) / peak
                if dd > max_dd:
                    max_dd = dd

        duration_days = max((end_time - start_time).total_seconds() / 86400.0, 1.0)
        annualized = max(1.0 + total_return, 0.0) ** (365.0 / duration_days) - 1.0

        # 计算夏普
        sharpe: float | None = None
        if len(equity_curve) >= 2:
            returns = [
                (equity_curve[j] - equity_curve[j - 1]) / equity_curve[j - 1]
                for j in range(1, len(equity_curve))
                if equity_curve[j - 1] > 0
            ]
            if returns:
                m_ret = sum(returns) / len(returns)
                var = sum((r - m_ret) ** 2 for r in returns) / len(returns)
                std_ret = math.sqrt(var)
                if std_ret > 1e-8:
                    # 按观测区间平均采样频率年化；不等间隔数据仍是近似。
                    elapsed = (end_time - start_time).total_seconds()
                    if elapsed > 0:
                        annual_periods = len(returns) * 365.0 * 86400.0 / elapsed
                        sharpe = round((m_ret / std_ret) * math.sqrt(annual_periods), 4)

        win_rate = winning_trades / closed_trades if closed_trades > 0 else 0.0

        summary = (
            f"影子运行完成：总收益率 {total_return * 100:.2f}%，最大回撤 {max_dd * 100:.2f}%，"
            f"交易笔数 {trades_count}，夏普比率 {sharpe}；模型上下文预热弃权 {warmup_count} 次；未计算多重检验修正指标"
        )

        return ShadowRunReport(
            run_id=run_id,
            start_time=start_time,
            end_time=end_time,
            initial_cash=self._initial_cash,
            final_equity=round(final_equity, 2),
            total_return_pct=round(total_return, 6),
            annualized_return_pct=round(annualized, 6),
            max_drawdown_pct=round(max_dd, 6),
            sharpe_ratio=sharpe,
            total_trades=trades_count,
            winning_trades=winning_trades,
            win_rate=round(win_rate, 4),
            total_fees_cny=round(total_fees, 2),
            summary=summary,
        )
