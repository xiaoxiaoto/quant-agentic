"""影子运行与消融验证仿真服务。

驱动行情流式回放，串联特征工程、模型预测、元决策、独立风控与 RFQ 成交模型。
核算净值曲线、最大回撤、交易成本与降缩夏普比率（Deflated Sharpe Ratio）。
"""

import math
import uuid
from datetime import UTC, datetime

from src.services.decision_engine import DecisionEngine
from src.services.feature_engine import FeatureEngine
from src.services.predictor import ModelPredictor
from src.services.rfq_execution import RFQExecutionEngine
from src.services.risk_engine import RiskEngine
from src.services.shadow_ledger import ShadowLedger
from src.types.domain.benchmark import ShadowRunReport
from src.types.domain.execution import RFQRequest
from src.types.domain.feature import ModelPrediction
from src.types.domain.product import FeeRule
from src.types.domain.quote import QuoteRaw
from src.types.enums.trading import DecisionAction, HoldingPeriod, TradeDirection


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
        self._initial_cash = initial_cash
        self._fee_rule = fee_rule or FeeRule()
        self._features = feature_engine or FeatureEngine()
        self._predictor = predictor or ModelPredictor()
        self._decision = decision_engine or DecisionEngine()
        self._risk = risk_engine or RiskEngine(fee_rule=self._fee_rule)
        self._rfq = rfq_engine or RFQExecutionEngine(fee_rule=self._fee_rule)

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _calc_deflated_sharpe(
        self,
        sharpe: float | None,
        num_trials: int = 10,
        sample_length: int = 100,
    ) -> float | None:
        """根据多重检验试验次数对夏普比率进行降缩修正（Deflated Sharpe）。"""
        if sharpe is None or sample_length < 5:
            return None
        # 极大值期望近似：E[max] ≈ sqrt(2 * log(N))
        e_max_sharpe = math.sqrt(2.0 * math.log(max(num_trials, 1)))
        # 降缩调整后的估计量
        deflated = sharpe - (e_max_sharpe / math.sqrt(sample_length))
        return round(max(deflated, 0.0), 4)

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

        sorted_quotes = sorted(quotes, key=lambda q: q.quote_at)
        start_time = sorted_quotes[0].quote_at
        end_time = sorted_quotes[-1].quote_at

        ledger = ShadowLedger(initial_cash=self._initial_cash, fee_rule=self._fee_rule)
        equity_curve: list[float] = []
        trades_count = 0
        winning_trades = 0
        total_fees = 0.0

        for i in range(len(sorted_quotes)):
            curr_q = sorted_quotes[i]
            history_window = sorted_quotes[max(0, i - 30) : i + 1]

            # 1. 提取特征
            feats = self._features.extract_features(history_window)

            # 2. 预测主要持有周期
            pred_w1 = self._predictor.predict(feats, horizon=HoldingPeriod.W1)
            pred_d1 = self._predictor.predict(feats, horizon=HoldingPeriod.D1)
            preds: dict[HoldingPeriod, ModelPrediction] = {
                HoldingPeriod.W1: pred_w1,
                HoldingPeriod.D1: pred_d1,
            }

            # 3. 元决策引擎评估
            snap = ledger.take_snapshot(curr_q.quote_at)
            dec = self._decision.evaluate_decision(
                quote=curr_q,
                predictions=preds,
                account=snap,
                target_buy_grams=trade_grams_per_order,
            )

            # 4. 独立风控一票否决
            approved_dec, _, violations = self._risk.check_decision(
                decision=dec,
                account=snap,
                current_ask_price=curr_q.ask_price,
                current_bid_price=curr_q.bid_price,
            )

            # 5. RFQ 成交模型仿真
            if approved_dec.action in (DecisionAction.BUY, DecisionAction.SELL):
                direction = TradeDirection.BUY if approved_dec.action == DecisionAction.BUY else TradeDirection.SELL
                req = RFQRequest(
                    request_id=f"req_{i}",
                    direction=direction,
                    grams=abs(approved_dec.delta_g),
                    decision_time=curr_q.quote_at,
                    eligible_at=curr_q.quote_at,
                    is_programmatic=True,
                )
                fill = self._rfq.simulate_execution(req, candidate_quotes=sorted_quotes[i:])
                if fill.is_filled and fill.filled_price is not None:
                    trades_count += 1
                    total_fees += fill.fee_cny
                    if direction == TradeDirection.BUY:
                        ledger.record_buy(
                            client_order_id=f"cid_{i}",
                            grams=fill.filled_grams,
                            price=fill.filled_price,
                            fee=fill.fee_cny,
                            timestamp=curr_q.quote_at,
                        )
                    else:
                        event = ledger.record_sell(
                            client_order_id=f"cid_{i}",
                            grams=fill.filled_grams,
                            price=fill.filled_price,
                            fee=fill.fee_cny,
                            timestamp=curr_q.quote_at,
                        )
                        # 检查已实现盈亏是否为正
                        current_snap = ledger.take_snapshot(curr_q.quote_at)
                        if current_snap.realized_pnl > 0:
                            winning_trades += 1

            # 记录净值曲线
            eq = ledger.get_total_equity(curr_q.bid_price)
            equity_curve.append(eq)

        # 结算绩效指标
        final_equity = equity_curve[-1] if equity_curve else self._initial_cash
        total_return = (final_equity - self._initial_cash) / self._initial_cash

        # 计算最大回撤
        peak = equity_curve[0] if equity_curve else self._initial_cash
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            elif peak > 0:
                dd = (peak - eq) / peak
                if dd > max_dd:
                    max_dd = dd

        duration_days = max((end_time - start_time).total_seconds() / 86400.0, 1.0)
        annualized = (1.0 + total_return) ** (365.0 / duration_days) - 1.0

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
                    sharpe = round((m_ret / std_ret) * math.sqrt(250.0), 4)

        deflated_sr = self._calc_deflated_sharpe(sharpe, sample_length=len(equity_curve))
        win_rate = winning_trades / trades_count if trades_count > 0 else 0.0

        summary = (
            f"影子运行完成：总收益率 {total_return * 100:.2f}%，最大回撤 {max_dd * 100:.2f}%，"
            f"交易笔数 {trades_count}，修正后夏普比率 {deflated_sr}"
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
            deflated_sharpe=deflated_sr,
            total_trades=trades_count,
            winning_trades=winning_trades,
            win_rate=round(win_rate, 4),
            total_fees_cny=round(total_fees, 2),
            summary=summary,
        )
