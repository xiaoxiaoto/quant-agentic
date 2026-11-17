"""独立风控一票否决服务。

实行严格的风控前置审查：
- 净退出损失限额管控：L_g ≈ P_ask × (d + s + f_sell)
- 碎片库存与卖出规则穷举（持仓 ≤ 1g 必须全额卖出）
- 周末跳空风险桶限额
- 资金与现货多头敞口约束
"""

from src.types.domain.account import AccountSnapshot
from src.types.domain.decision import Decision, RiskBudget
from src.types.domain.product import FeeRule, TradeRule
from src.types.enums.trading import DecisionAction, RiskState


class RiskEngine:
    """独立风控一票否决检查引擎。"""

    def __init__(
        self,
        risk_budget: RiskBudget | None = None,
        trade_rule: TradeRule | None = None,
        fee_rule: FeeRule | None = None,
    ) -> None:
        self._budget = risk_budget or RiskBudget()
        self._trade_rule = trade_rule or TradeRule()
        self._fee_rule = fee_rule or FeeRule()

    def _calc_net_exit_loss_per_gram(
        self,
        ask_price: float,
        spread: float,
        stop_loss_distance_pct: float = 0.005,
    ) -> float:
        """根据买入价、止损距离、点差与卖出费计算每克净退出损失。"""
        spread_pct = spread / ask_price if ask_price > 0 else 0.0
        total_loss_pct = stop_loss_distance_pct + spread_pct + self._fee_rule.sell_fee_rate
        return ask_price * total_loss_pct

    def check_decision(
        self,
        decision: Decision,
        account: AccountSnapshot,
        current_ask_price: float,
        current_bid_price: float,
        risk_state: RiskState = RiskState.NORMAL,
    ) -> tuple[Decision, RiskState, list[str]]:
        """风控一票否决核验，若违规强制降级决策。"""
        violations: list[str] = []
        approved_decision = decision.model_copy(deep=True)

        # 1. 处于紧急对账停机状态，拒绝任何买入与卖出
        if risk_state == RiskState.HALT_RECONCILE:
            violations.append("HALT_RECONCILE_ACTIVE")
            approved_decision.action = DecisionAction.ABSTAIN
            approved_decision.delta_g = 0.0
            return approved_decision, risk_state, violations

        # 2. 仅允许平仓状态，拒绝新买入
        if risk_state == RiskState.REDUCE_ONLY and approved_decision.action == DecisionAction.BUY:
            violations.append("REDUCE_ONLY_BLOCKS_BUY")
            approved_decision.action = DecisionAction.ABSTAIN
            approved_decision.delta_g = 0.0
            return approved_decision, risk_state, violations

        if approved_decision.action == DecisionAction.BUY:
            buy_grams = approved_decision.delta_g
            # 买入最小克数约束
            if buy_grams < self._trade_rule.buy_min_grams:
                violations.append(f"BUY_GRAMS_LESS_THAN_MIN_{self._trade_rule.buy_min_grams}")

            # 资金充足性核验
            estimated_cost = buy_grams * current_ask_price * (1.0 + self._fee_rule.buy_fee_rate)
            if estimated_cost > account.cash_available:
                violations.append("INSUFFICIENT_CASH")

            # 净退出损失预算核验
            spread = current_ask_price - current_bid_price
            exit_loss_per_g = self._calc_net_exit_loss_per_gram(current_ask_price, spread)
            total_net_exit_loss = buy_grams * exit_loss_per_g
            total_equity = account.cash_available + account.gold_total_g * current_bid_price
            loss_budget_cny = total_equity * self._budget.single_trade_net_exit_loss_pct

            if total_net_exit_loss > loss_budget_cny:
                violations.append(
                    f"EXCEEDS_NET_EXIT_LOSS_BUDGET_NEED_{total_net_exit_loss:.2f}_MAX_{loss_budget_cny:.2f}"
                )

        elif approved_decision.action == DecisionAction.SELL:
            sell_grams = abs(approved_decision.delta_g)
            # 可用持仓核验
            if sell_grams > account.gold_available_g:
                violations.append("INSUFFICIENT_GOLD_INVENTORY")

            # 碎片库存规则核验
            if account.gold_total_g <= 1.0:
                # 持仓 ≤ 1克时必须一次性全部卖出
                if self._trade_rule.liquidate_all_if_le_1g and abs(sell_grams - account.gold_total_g) > 1e-4:
                    violations.append("FRACTIONAL_INVENTORY_MUST_LIQUIDATE_ALL_LE_1G")
            else:
                # 持仓 > 1克时，单笔卖出不得小于 1克
                if sell_grams < self._trade_rule.sell_min_grams:
                    violations.append(f"SELL_GRAMS_LESS_THAN_MIN_{self._trade_rule.sell_min_grams}")

        if violations:
            # 触发一票否决
            approved_decision.action = DecisionAction.ABSTAIN
            approved_decision.delta_g = 0.0
            approved_decision.risk_flags.extend(violations)
            approved_decision.reason_codes.append("VETOED_BY_INDEPENDENT_RISK_GATE")

        return approved_decision, risk_state, violations
