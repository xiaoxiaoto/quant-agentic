"""独立风控一票否决服务。

实行严格的风控前置审查：
- 净退出损失限额管控：L_g ≈ P_ask × (d + s + f_sell)
- 碎片库存与卖出规则穷举（持仓 ≤ 1g 必须全额卖出）
- 资金与现货多头敞口约束
"""

import math

from src.contracts.domains.account import AccountSnapshot
from src.contracts.domains.decision import Decision, RiskBudget
from src.contracts.domains.product import FeeRule, TradeRule
from src.contracts.enums.trading import DecisionAction, RiskState
from src.services.decision.cost_calculator import CostCalculator


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
        self._calculator = CostCalculator(self._fee_rule)

    def check_decision(
        self,
        decision: Decision,
        account: AccountSnapshot,
        current_ask_price: float,
        current_bid_price: float,
        risk_state: RiskState = RiskState.NORMAL,
        *,
        daily_pnl: float | None = None,
        weekly_pnl: float | None = None,
    ) -> tuple[Decision, RiskState, list[str]]:
        """风控一票否决核验，若违规强制降级决策并在触发日/周亏损线时升级风控状态。"""
        violations: list[str] = []
        next_risk_state = risk_state
        approved_decision = decision.model_copy(deep=True)

        total_equity = account.cash_available + account.cash_frozen + account.gold_total_g * current_bid_price
        if total_equity > 0 and math.isfinite(total_equity):
            if (
                weekly_pnl is not None
                and math.isfinite(weekly_pnl)
                and self._budget.weekly_loss_trigger_pct is not None
                and weekly_pnl < 0
                and abs(weekly_pnl) / total_equity >= self._budget.weekly_loss_trigger_pct
            ):
                if next_risk_state != RiskState.HALT_RECONCILE:
                    next_risk_state = RiskState.REDUCE_ONLY
                if approved_decision.action == DecisionAction.BUY:
                    violations.append("WEEKLY_LOSS_LIMIT_BREACHED")
            elif (
                daily_pnl is not None
                and math.isfinite(daily_pnl)
                and daily_pnl < 0
                and abs(daily_pnl) / total_equity >= self._budget.daily_loss_trigger_pct
            ):
                if next_risk_state == RiskState.NORMAL:
                    next_risk_state = RiskState.NO_NEW_RISK
                if approved_decision.action == DecisionAction.BUY:
                    violations.append("DAILY_LOSS_LIMIT_BREACHED")

        is_trade = approved_decision.action in (DecisionAction.BUY, DecisionAction.SELL)
        if next_risk_state == RiskState.HALT_RECONCILE:
            violations.append("HALT_RECONCILE_ACTIVE")
        elif (
            next_risk_state in (RiskState.NO_NEW_RISK, RiskState.REDUCE_ONLY)
            and approved_decision.action == DecisionAction.BUY
            and not any(v in violations for v in ("DAILY_LOSS_LIMIT_BREACHED", "WEEKLY_LOSS_LIMIT_BREACHED"))
        ):
            violations.append(f"{next_risk_state.value}_BLOCKS_BUY")
        elif is_trade and (
            not all(math.isfinite(value) for value in (current_ask_price, current_bid_price, approved_decision.delta_g))
            or current_bid_price <= 0
            or current_ask_price < current_bid_price
        ):
            violations.append("INVALID_TRADE_PRICE_OR_QUANTITY")
        elif is_trade and (
            (approved_decision.action == DecisionAction.BUY and approved_decision.delta_g <= 0)
            or (approved_decision.action == DecisionAction.SELL and approved_decision.delta_g >= 0)
        ):
            violations.append("INVALID_TRADE_DIRECTION")
        elif approved_decision.action == DecisionAction.BUY:
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
            exit_loss_per_g = self._calculator.net_exit_loss_per_gram(
                current_ask_price, 0.005, spread / current_ask_price
            )
            total_net_exit_loss = buy_grams * exit_loss_per_g
            loss_budget_cny = total_equity * self._budget.single_trade_net_exit_loss_pct

            if total_net_exit_loss > loss_budget_cny:
                violations.append(
                    f"EXCEEDS_NET_EXIT_LOSS_BUDGET_NEED_{total_net_exit_loss:.2f}_MAX_{loss_budget_cny:.2f}"
                )

            exposure_limit = self._budget.max_gold_exposure_pct
            projected_equity = total_equity - estimated_cost + buy_grams * current_bid_price
            projected_gold_value = (account.gold_total_g + buy_grams) * current_bid_price
            if exposure_limit is not None and projected_gold_value > projected_equity * exposure_limit:
                violations.append("MAX_GOLD_EXPOSURE_EXCEEDED")

        elif approved_decision.action == DecisionAction.SELL:
            sell_grams = abs(approved_decision.delta_g)
            # 可用持仓核验
            if sell_grams > account.gold_available_g:
                violations.append("INSUFFICIENT_GOLD_INVENTORY")

            # 碎片库存规则核验
            if account.gold_total_g <= self._trade_rule.sell_min_grams:
                # 持仓 ≤ 最小卖出克重时必须一次性全部卖出
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
            approved_decision.target_inventory_g = account.gold_total_g
            approved_decision.risk_flags.extend(violations)
            approved_decision.reason_codes.append("VETOED_BY_INDEPENDENT_RISK_GATE")

        return approved_decision, next_risk_state, violations
