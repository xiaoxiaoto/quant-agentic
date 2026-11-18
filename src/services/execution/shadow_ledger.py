"""影子账本服务模块。

以系统本地视角维护资金、持仓克重、平均成本与已实现/未实现盈亏。
银行账单为唯一权威，影子账本通过持续对账保证一致性。
"""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
from threading import RLock

from src.contracts.domains.account import AccountSnapshot, OrderEvent
from src.contracts.domains.product import FeeRule
from src.contracts.enums.trading import OrderState, TradeDirection


class ShadowLedger:
    """本地影子账本核心引擎。

    精确记录现金流水、持仓成本变动与交易事件。
    """

    def __init__(
        self,
        initial_cash: float = 100000.0,
        fee_rule: FeeRule | None = None,
    ) -> None:
        if not isfinite(initial_cash) or initial_cash < 0:
            raise ValueError("初始资金必须为非负有限值")
        self._lock = RLock()
        self._cash_available: Decimal = Decimal(str(initial_cash))
        self._cash_frozen: Decimal = Decimal("0.0")
        self._gold_total_g: Decimal = Decimal("0.0")
        self._gold_available_g: Decimal = Decimal("0.0")
        self._gold_frozen_g: Decimal = Decimal("0.0")
        self._cost_basis_per_g: Decimal | None = None
        self._realized_pnl: Decimal = Decimal("0.0")
        self._unrealized_pnl: Decimal = Decimal("0.0")
        self._interest_pending_g: Decimal = Decimal("0.0")
        self._interest_credited_g: Decimal = Decimal("0.0")
        self._fee_rule: FeeRule = fee_rule or FeeRule()
        self._order_history: list[OrderEvent] = []
        self._orders_by_id: dict[str, OrderEvent] = {}

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _round_cash(self, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _round_gold(self, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    def _existing_trade(
        self,
        client_order_id: str,
        direction: TradeDirection,
        grams: float,
        price: float,
        fee: Decimal,
    ) -> OrderEvent | None:
        if grams <= 0 or price <= 0 or fee < 0:
            raise ValueError("成交克重和价格必须为正，手续费不得为负")
        if Decimal(str(grams)) != self._round_gold(Decimal(str(grams))):
            raise ValueError("成交克重不得超过四位小数")
        existing = self._orders_by_id.get(client_order_id)
        if existing is not None:
            if (existing.direction, existing.filled_grams, existing.execution_price, existing.fee) != (
                direction,
                grams,
                price,
                float(fee),
            ):
                raise ValueError("同一订单号的成交内容冲突")
            return existing.model_copy(deep=True)
        return None

    def record_buy(
        self,
        client_order_id: str,
        grams: float,
        price: float,
        fee: float | None = None,
        bank_order_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> OrderEvent:
        """记录一笔买入成交并更新持仓与摊薄成本。"""
        with self._lock:
            if not all(isfinite(value) for value in (grams, price, fee if fee is not None else 0.0)):
                raise ValueError("成交数值必须为有限值")
            dt = timestamp or self._now()
            g_dec = Decimal(str(grams))
            p_dec = Decimal(str(price))
            f_rate = Decimal(str(self._fee_rule.buy_fee_rate))
            fee_dec = Decimal(str(fee)) if fee is not None else self._round_cash(g_dec * p_dec * f_rate)

            existing = self._existing_trade(client_order_id, TradeDirection.BUY, grams, price, fee_dec)
            if existing is not None:
                return existing

            trade_cost = self._round_cash(g_dec * p_dec)
            total_outflow = trade_cost + fee_dec

            if self._cash_available < total_outflow:
                raise ValueError(f"可用资金不足：需 {total_outflow}，当前可用 {self._cash_available}")

            # 计算摊薄加权成本
            old_total_g = self._gold_total_g
            old_cost = self._cost_basis_per_g or Decimal("0.0")
            new_total_g = old_total_g + g_dec

            if new_total_g > Decimal("0.0"):
                new_cost_basis = (old_total_g * old_cost + total_outflow) / new_total_g
                self._cost_basis_per_g = new_cost_basis
            else:
                self._cost_basis_per_g = None

            self._cash_available -= total_outflow
            self._gold_total_g = self._round_gold(new_total_g)
            self._gold_available_g = self._round_gold(self._gold_available_g + g_dec)

            event = OrderEvent(
                client_order_id=client_order_id,
                bank_order_id=bank_order_id,
                order_type="MARKET",
                direction=TradeDirection.BUY,
                requested_grams=grams,
                filled_grams=grams,
                execution_price=price,
                fee=float(fee_dec),
                state=OrderState.FILLED,
                bank_event_at=dt,
                received_at=dt,
            )
            self._order_history.append(event)
            self._orders_by_id[client_order_id] = event
            self._unrealized_pnl = Decimal("0.0")
            return event.model_copy(deep=True)

    def record_sell(
        self,
        client_order_id: str,
        grams: float,
        price: float,
        fee: float | None = None,
        bank_order_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> OrderEvent:
        """记录一笔卖出成交并核算已实现盈亏。"""
        with self._lock:
            if not all(isfinite(value) for value in (grams, price, fee if fee is not None else 0.0)):
                raise ValueError("成交数值必须为有限值")
            dt = timestamp or self._now()
            g_dec = Decimal(str(grams))
            p_dec = Decimal(str(price))

            f_rate = Decimal(str(self._fee_rule.sell_fee_rate))
            fee_dec = Decimal(str(fee)) if fee is not None else self._round_cash(g_dec * p_dec * f_rate)

            existing = self._existing_trade(client_order_id, TradeDirection.SELL, grams, price, fee_dec)
            if existing is not None:
                return existing

            if self._gold_available_g < g_dec:
                raise ValueError(f"可用黄金克重不足：需 {g_dec}g，当前可用 {self._gold_available_g}g")

            gross_revenue = self._round_cash(g_dec * p_dec)
            net_inflow = gross_revenue - fee_dec

            # 核算该笔卖出已实现盈亏
            cost_basis = self._cost_basis_per_g or Decimal("0.0")
            sold_cost = self._round_cash(g_dec * cost_basis)
            pnl = net_inflow - sold_cost
            self._realized_pnl += pnl

            # 扣减持仓与增加资金
            self._gold_total_g = self._round_gold(self._gold_total_g - g_dec)
            self._gold_available_g = self._round_gold(self._gold_available_g - g_dec)
            self._cash_available += net_inflow

            if self._gold_total_g == Decimal("0.0"):
                self._cost_basis_per_g = None

            event = OrderEvent(
                client_order_id=client_order_id,
                bank_order_id=bank_order_id,
                order_type="MARKET",
                direction=TradeDirection.SELL,
                requested_grams=grams,
                filled_grams=grams,
                execution_price=price,
                fee=float(fee_dec),
                state=OrderState.FILLED,
                bank_event_at=dt,
                received_at=dt,
            )
            self._order_history.append(event)
            self._orders_by_id[client_order_id] = event
            self._unrealized_pnl = Decimal("0.0")
            return event.model_copy(deep=True)

    def mark_to_market(self, current_bid_price: float) -> Decimal:
        """根据最新买入/卖出中间价或客户卖出价重估浮动盈亏。"""
        with self._lock:
            if self._gold_total_g <= Decimal("0.0") or self._cost_basis_per_g is None:
                self._unrealized_pnl = Decimal("0.0")
                return self._unrealized_pnl

            bid_dec = Decimal(str(current_bid_price))
            f_rate = Decimal(str(self._fee_rule.sell_fee_rate))
            estimated_sell_revenue = self._gold_total_g * bid_dec * (Decimal("1.0") - f_rate)
            holding_cost = self._gold_total_g * self._cost_basis_per_g
            self._unrealized_pnl = self._round_cash(estimated_sell_revenue - holding_cost)
            return self._unrealized_pnl

    def take_snapshot(self, as_of: datetime | None = None) -> AccountSnapshot:
        """生成当前账本资产快照。"""
        with self._lock:
            return AccountSnapshot(
                cash_available=float(self._cash_available),
                cash_frozen=float(self._cash_frozen),
                gold_total_g=float(self._gold_total_g),
                gold_available_g=float(self._gold_available_g),
                gold_frozen_g=float(self._gold_frozen_g),
                cost_basis_per_g=float(self._cost_basis_per_g) if self._cost_basis_per_g else None,
                realized_pnl=float(self._realized_pnl),
                unrealized_pnl=float(self._unrealized_pnl),
                interest_pending_g=float(self._interest_pending_g),
                interest_credited_g=float(self._interest_credited_g),
                as_of=as_of or self._now(),
            )

    def get_total_equity(self, current_bid_price: float) -> float:
        """计算当前账户动态总净值（现金 + 黄金市值折现）。"""
        with self._lock:
            self.mark_to_market(current_bid_price)
            bid_dec = Decimal(str(current_bid_price))
            gold_value = self._gold_total_g * bid_dec
            total = self._cash_available + self._cash_frozen + gold_value
            return float(self._round_cash(total))

    def get_order_history(self) -> list[OrderEvent]:
        """获取所有历史订单流水事件副本。"""
        with self._lock:
            return [event.model_copy(deep=True) for event in self._order_history]
