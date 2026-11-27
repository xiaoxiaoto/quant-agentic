"""影子账本服务模块。

以系统本地视角维护资金、持仓克重、平均成本与已实现/未实现盈亏。
银行账单为唯一权威，影子账本通过持续对账保证一致性。
"""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
from threading import RLock
from typing import Literal
from zoneinfo import ZoneInfo

from src.contracts.domains.account import AccountSnapshot, OrderEvent
from src.contracts.domains.product import FeeRule
from src.contracts.enums.trading import OrderState, TradeDirection
from src.contracts.schemas.trading import FifoLotItem, FifoSummaryResponse
from src.repositories.runtime_state import RuntimeStateRepository


class ShadowLedger:
    """本地影子账本核心引擎。

    精确记录现金流水、持仓成本变动与交易事件。
    """

    def __init__(
        self,
        initial_cash: float = 100000.0,
        fee_rule: FeeRule | None = None,
        state_store: RuntimeStateRepository | None = None,
        *,
        scope: str = "live",
    ) -> None:
        if not isfinite(initial_cash) or initial_cash < 0:
            raise ValueError("初始资金必须为非负有限值")
        self._lock = RLock()
        self._state_store = state_store
        self._scope = scope
        self._restoring = False
        self._initial_cash: Decimal = self._round_cash(Decimal(str(initial_cash)))
        self._cash_available: Decimal = self._initial_cash
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
        self._realized_events: list[tuple[datetime, Decimal]] = []
        if self._state_store is not None:
            self._restore_from_store()

    def _compute_open_position_cost(self, orders: list[OrderEvent]) -> Decimal:
        """按移动加权平均口径核算当前在持净克重的持仓总成本（含买入分摊手续费）。"""
        holding_g = Decimal("0.0")
        cost_basis: Decimal | None = None
        for order in orders:
            if order.filled_grams is None or order.execution_price is None:
                continue
            g = Decimal(str(order.filled_grams))
            p = Decimal(str(order.execution_price))
            fee = Decimal(str(order.fee or 0.0))
            if order.direction == TradeDirection.BUY:
                outflow = self._round_cash(g * p) + self._round_cash(fee)
                prev_cost = cost_basis or Decimal("0.0")
                new_g = holding_g + g
                cost_basis = (holding_g * prev_cost + outflow) / new_g if new_g > Decimal("0.0") else None
                holding_g = self._round_gold(new_g)
            elif order.direction == TradeDirection.SELL:
                if holding_g < g:
                    g = holding_g
                holding_g = self._round_gold(holding_g - g)
                if holding_g <= Decimal("0.0"):
                    holding_g = Decimal("0.0")
                    cost_basis = None
        if holding_g <= Decimal("0.0") or cost_basis is None:
            return Decimal("0.0")
        return self._round_cash(holding_g * cost_basis)

    def _restore_from_store(self) -> None:
        """从持久化仓储重放历史成交事件与初始化元数据以恢复准确账本余额与成本。"""
        if self._state_store is None:
            return
        self._restoring = True
        try:
            meta = self._state_store.load_ledger_meta(scope=self._scope)
            interest_adjust = Decimal("0.0")
            if meta is not None:
                meta_initial_cash, meta_interest_adjust = meta
                self._initial_cash = self._round_cash(meta_initial_cash)
                self._cash_available = self._initial_cash
                interest_adjust = self._round_gold(max(Decimal("0.0"), meta_interest_adjust))

            loaded = self._state_store.load_orders(scope=self._scope)
            jd_prefix: list[OrderEvent] = []
            jd_anchored = False
            interest_applied = False
            for order in loaded:
                if order.filled_grams is None or order.execution_price is None:
                    continue
                is_jd_history = order.client_order_id.startswith("jd_") and not order.client_order_id.startswith(
                    "jd_init_"
                )
                if not is_jd_history and jd_prefix and not jd_anchored:
                    open_cost = self._compute_open_position_cost(jd_prefix)
                    self._cash_available = self._round_cash(max(Decimal("0.0"), self._initial_cash - open_cost))
                    if interest_adjust > Decimal("0.0") and not interest_applied:
                        self._credit_interest_gold(interest_adjust)
                        interest_applied = True
                    jd_anchored = True
                if order.direction == TradeDirection.BUY:
                    self.record_buy(
                        client_order_id=order.client_order_id,
                        grams=order.filled_grams,
                        price=order.execution_price,
                        fee=order.fee,
                        bank_order_id=order.bank_order_id,
                        timestamp=order.bank_event_at or order.received_at,
                        order_type=order.order_type,
                    )
                else:
                    self.record_sell(
                        client_order_id=order.client_order_id,
                        grams=order.filled_grams,
                        price=order.execution_price,
                        fee=order.fee,
                        bank_order_id=order.bank_order_id,
                        timestamp=order.bank_event_at or order.received_at,
                        order_type=order.order_type,
                        allow_interest_shortfall=True,
                    )
                if is_jd_history and not jd_anchored:
                    jd_prefix.append(order)
            if jd_prefix and not jd_anchored:
                open_cost = self._compute_open_position_cost(jd_prefix)
                self._cash_available = self._round_cash(max(Decimal("0.0"), self._initial_cash - open_cost))
            if interest_adjust > Decimal("0.0") and not interest_applied:
                self._credit_interest_gold(interest_adjust)
        finally:
            self._restoring = False

    def _credit_interest_gold(self, interest_g: Decimal) -> None:
        """计入零成本生息克重并按总克重摊薄持仓均价成本。"""
        if interest_g <= Decimal("0.0"):
            return
        old_total = self._gold_total_g
        new_total = self._round_gold(old_total + interest_g)
        if old_total > Decimal("0.0") and self._cost_basis_per_g is not None and new_total > Decimal("0.0"):
            self._cost_basis_per_g = (old_total * self._cost_basis_per_g) / new_total
        elif old_total <= Decimal("0.0"):
            self._cost_basis_per_g = Decimal("0.0")
        self._gold_total_g = new_total
        self._gold_available_g = self._round_gold(self._gold_available_g + interest_g)
        self._interest_credited_g = self._round_gold(self._interest_credited_g + interest_g)

    def _persist_order(self, order: OrderEvent) -> None:
        """在启用状态仓储且非恢复阶段时增量落盘单笔成交订单。"""
        if self._state_store is not None and not self._restoring:
            self._state_store.upsert_order(order, scope=self._scope)

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

    @property
    def sell_fee_rate(self) -> float:
        """返回当前账本生效的卖出手续费率。"""
        return self._fee_rule.sell_fee_rate

    def record_buy(
        self,
        client_order_id: str,
        grams: float,
        price: float,
        fee: float | None = None,
        bank_order_id: str | None = None,
        timestamp: datetime | None = None,
        order_type: str = "MARKET",
    ) -> OrderEvent:
        """记录一笔买入成交并更新持仓与摊薄成本。"""
        with self._lock:
            if not all(isfinite(value) for value in (grams, price, fee if fee is not None else 0.0)):
                raise ValueError("成交数值必须为有限值")
            dt = timestamp or self._now()
            g_dec = Decimal(str(grams))
            p_dec = Decimal(str(price))
            f_rate = Decimal(str(self._fee_rule.buy_fee_rate))
            fee_dec = (
                self._round_cash(Decimal(str(fee))) if fee is not None else self._round_cash(g_dec * p_dec * f_rate)
            )

            existing = self._existing_trade(client_order_id, TradeDirection.BUY, grams, price, fee_dec)
            if existing is not None:
                return existing

            trade_cost = self._round_cash(g_dec * p_dec)
            total_outflow = trade_cost + fee_dec

            if self._cash_available < total_outflow:
                raise ValueError(f"可用资金不足：需 {total_outflow}，当前可用 {self._cash_available}")

            # 保存旧状态，落盘失败时用于回滚
            prev_cash = self._cash_available
            prev_gold_total = self._gold_total_g
            prev_gold_avail = self._gold_available_g
            prev_cost_basis = self._cost_basis_per_g

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
                order_type=order_type,
                direction=TradeDirection.BUY,
                requested_grams=grams,
                filled_grams=grams,
                execution_price=price,
                fee=float(fee_dec),
                state=OrderState.FILLED,
                bank_event_at=dt,
                received_at=dt,
            )

            # 先持久化，落盘失败时回滚内存状态
            try:
                self._persist_order(event)
            except Exception:
                self._cash_available = prev_cash
                self._gold_total_g = prev_gold_total
                self._gold_available_g = prev_gold_avail
                self._cost_basis_per_g = prev_cost_basis
                raise
            self._order_history.append(event)
            self._orders_by_id[client_order_id] = event
            self.mark_to_market(price)
            return event.model_copy(deep=True)

    def record_sell(
        self,
        client_order_id: str,
        grams: float,
        price: float,
        fee: float | None = None,
        bank_order_id: str | None = None,
        timestamp: datetime | None = None,
        order_type: str = "MARKET",
        allow_interest_shortfall: bool = False,
    ) -> OrderEvent:
        """记录一笔卖出成交并核算已实现盈亏。"""
        with self._lock:
            if not all(isfinite(value) for value in (grams, price, fee if fee is not None else 0.0)):
                raise ValueError("成交数值必须为有限值")
            dt = timestamp or self._now()
            g_dec = Decimal(str(grams))
            p_dec = Decimal(str(price))

            f_rate = Decimal(str(self._fee_rule.sell_fee_rate))
            fee_dec = (
                self._round_cash(Decimal(str(fee))) if fee is not None else self._round_cash(g_dec * p_dec * f_rate)
            )

            existing = self._existing_trade(client_order_id, TradeDirection.SELL, grams, price, fee_dec)
            if existing is not None:
                return existing

            # 保存旧状态，落盘失败时用于回滚
            prev_cash = self._cash_available
            prev_gold_total = self._gold_total_g
            prev_gold_avail = self._gold_available_g
            prev_cost_basis = self._cost_basis_per_g
            prev_realized_pnl = self._realized_pnl
            prev_interest_credited = self._interest_credited_g
            prev_realized_events_len = len(self._realized_events)

            if self._gold_available_g < g_dec:
                if allow_interest_shortfall:
                    shortfall = self._round_gold(g_dec - self._gold_available_g)
                    self._credit_interest_gold(shortfall)
                else:
                    raise ValueError(f"可用黄金克重不足：需 {g_dec}g，当前可用 {self._gold_available_g}g")

            gross_revenue = self._round_cash(g_dec * p_dec)
            net_inflow = gross_revenue - fee_dec

            # 核算该笔卖出已实现盈亏
            cost_basis = self._cost_basis_per_g or Decimal("0.0")
            sold_cost = self._round_cash(g_dec * cost_basis)
            pnl = net_inflow - sold_cost
            self._realized_pnl += pnl
            dt_utc = dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
            self._realized_events.append((dt_utc, pnl))

            # 扣减持仓与增加资金
            self._gold_total_g = self._round_gold(self._gold_total_g - g_dec)
            self._gold_available_g = self._round_gold(self._gold_available_g - g_dec)
            self._cash_available += net_inflow

            if self._gold_total_g == Decimal("0.0"):
                self._cost_basis_per_g = None

            event = OrderEvent(
                client_order_id=client_order_id,
                bank_order_id=bank_order_id,
                order_type=order_type,
                direction=TradeDirection.SELL,
                requested_grams=grams,
                filled_grams=grams,
                execution_price=price,
                fee=float(fee_dec),
                state=OrderState.FILLED,
                bank_event_at=dt,
                received_at=dt,
            )

            # 先持久化，落盘失败时回滚内存状态
            try:
                self._persist_order(event)
            except Exception:
                self._cash_available = prev_cash
                self._gold_total_g = prev_gold_total
                self._gold_available_g = prev_gold_avail
                self._cost_basis_per_g = prev_cost_basis
                self._realized_pnl = prev_realized_pnl
                self._interest_credited_g = prev_interest_credited
                self._realized_events = self._realized_events[:prev_realized_events_len]
                raise
            self._order_history.append(event)
            self._orders_by_id[client_order_id] = event
            if self._gold_total_g <= Decimal("0.0"):
                self._unrealized_pnl = Decimal("0.0")
            else:
                self.mark_to_market(price)
            return event.model_copy(deep=True)

    def mark_to_market(self, current_bid_price: float) -> Decimal:
        """根据最新买入/卖出中间价或客户卖出价重估浮动盈亏（扣除卖出费率）。"""
        if not isfinite(current_bid_price) or current_bid_price < 0:
            raise ValueError("重估价格必须为非负有限数")
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
                cost_basis_per_g=float(self._cost_basis_per_g) if self._cost_basis_per_g is not None else None,
                realized_pnl=float(self._realized_pnl),
                unrealized_pnl=float(self._unrealized_pnl),
                interest_pending_g=float(self._interest_pending_g),
                interest_credited_g=float(self._interest_credited_g),
                as_of=as_of or self._now(),
            )

    def get_total_equity(self, current_bid_price: float) -> float:
        """计算当前账户动态总市值（现金 + 黄金毛市值，不含未来卖出手续费）。"""
        if not isfinite(current_bid_price) or current_bid_price < 0:
            raise ValueError("重估价格必须为非负有限数")
        with self._lock:
            self.mark_to_market(current_bid_price)
            bid_dec = Decimal(str(current_bid_price))
            gold_value = self._gold_total_g * bid_dec
            total = self._cash_available + self._cash_frozen + gold_value
            return float(self._round_cash(total))

    def get_order_history(self, *, offset: int = 0, limit: int | None = None) -> list[OrderEvent]:
        """获取所有历史订单流水事件副本，支持分页。"""
        with self._lock:
            if limit is not None:
                page = self._order_history[offset : offset + limit]
            else:
                page = self._order_history[offset:]
            return [event.model_copy(deep=True) for event in page]

    def get_order_count(self) -> int:
        """获取历史订单总条数。"""
        with self._lock:
            return len(self._order_history)

    def reset_and_seed_holdings(
        self,
        *,
        initial_cash: float = 100000.0,
        holdings: list[tuple[str, float, float]],
    ) -> AccountSnapshot:
        """清空影子账本并按外部权威持仓列表初始化，可用现金 = initial_cash - 持仓总成本。"""
        if not isfinite(initial_cash) or initial_cash < 0:
            raise ValueError("初始资金必须为非负有限值")

        normalized: list[tuple[str, float, float]] = []
        total_cost = Decimal("0.0")
        for label, grams, avg_cost in holdings:
            if not isfinite(grams) or not isfinite(avg_cost) or grams <= 0 or avg_cost <= 0:
                continue
            rounded_g = float(self._round_gold(Decimal(str(grams))))
            if rounded_g <= 0:
                continue
            cost = self._round_cash(Decimal(str(rounded_g)) * Decimal(str(avg_cost)))
            total_cost += cost
            normalized.append((label.strip() or "HOLDING", rounded_g, avg_cost))

        budget = self._round_cash(Decimal(str(initial_cash)))
        if total_cost > budget:
            raise ValueError(f"持仓总成本 ¥{total_cost:.2f} 超过初始资金 ¥{budget:.2f}，请提高初始资金后重试")

        now_dt = self._now()
        sandbox = ShadowLedger(initial_cash=float(budget), fee_rule=self._fee_rule, state_store=None)
        ts_str = now_dt.strftime("%Y%m%d%H%M%S")
        for idx, (label, grams, avg_cost) in enumerate(normalized, start=1):
            sandbox.record_buy(
                client_order_id=f"jd_init_{label}_{ts_str}_{idx}",
                grams=grams,
                price=avg_cost,
                fee=0.0,
                bank_order_id=f"JD_HOLDING_{label}",
                timestamp=now_dt,
            )

        with self._lock:
            if self._state_store is not None:
                self._state_store.replace_orders(
                    sandbox._order_history,
                    scope=self._scope,
                    initial_cash=budget,
                    interest_adjust_g=Decimal("0.0"),
                )
            self._initial_cash = budget
            self._cash_available = sandbox._cash_available
            self._cash_frozen = Decimal("0.0")
            self._gold_total_g = sandbox._gold_total_g
            self._gold_available_g = sandbox._gold_available_g
            self._gold_frozen_g = Decimal("0.0")
            self._cost_basis_per_g = sandbox._cost_basis_per_g
            self._realized_pnl = Decimal("0.0")
            self._unrealized_pnl = Decimal("0.0")
            self._interest_pending_g = Decimal("0.0")
            self._interest_credited_g = Decimal("0.0")
            self._order_history = list(sandbox._order_history)
            self._orders_by_id = dict(sandbox._orders_by_id)
            self._realized_events = list(sandbox._realized_events)
            return self.take_snapshot(now_dt)

    def reset_and_replay_orders(
        self,
        *,
        initial_cash: float = 100000.0,
        orders: list[OrderEvent],
        target_gold_g: float | None = None,
    ) -> AccountSnapshot:
        """清空影子账本并按外部权威历史成交流水按时间顺序重放初始化。"""
        if not isfinite(initial_cash) or initial_cash < 0:
            raise ValueError("初始资金必须为非负有限值")
        if target_gold_g is not None and (not isfinite(target_gold_g) or target_gold_g < 0):
            raise ValueError("目标持仓克重必须为非负有限值")

        now_dt = self._now()
        ordered = sorted(
            (
                o
                for o in orders
                if o.filled_grams is not None
                and o.filled_grams > 0
                and o.execution_price is not None
                and o.execution_price > 0
            ),
            key=lambda item: item.bank_event_at or item.received_at or now_dt,
        )

        # 先在临时内存账本中校验整段流水重放不会出现资金不足等异常，保证原子性
        sandbox = ShadowLedger(initial_cash=initial_cash, fee_rule=self._fee_rule, state_store=None)
        for order in ordered:
            assert order.filled_grams is not None
            assert order.execution_price is not None
            ts = order.bank_event_at or order.received_at or now_dt
            if order.direction == TradeDirection.BUY:
                sandbox.record_buy(
                    client_order_id=order.client_order_id,
                    grams=order.filled_grams,
                    price=order.execution_price,
                    fee=order.fee,
                    bank_order_id=order.bank_order_id,
                    timestamp=ts,
                    order_type=order.order_type,
                )
            else:
                sandbox.record_sell(
                    client_order_id=order.client_order_id,
                    grams=order.filled_grams,
                    price=order.execution_price,
                    fee=order.fee,
                    bank_order_id=order.bank_order_id,
                    timestamp=ts,
                    order_type=order.order_type,
                    allow_interest_shortfall=True,
                )

        budget = self._round_cash(Decimal(str(initial_cash)))
        interest_diff = Decimal("0.0")
        # 若实盘总克重因积存金生息略高于纯买卖净克重，将生息差额记入已入账生息克重
        if target_gold_g is not None:
            target_dec = self._round_gold(Decimal(str(target_gold_g)))
            if target_dec > sandbox._gold_total_g:
                interest_diff = self._round_gold(target_dec - sandbox._gold_total_g)
                sandbox._credit_interest_gold(interest_diff)

        # 按初始资金减去当前在持批次总成本核算可用现金（在落盘与修改内存前完成校验）
        open_cost = self._compute_open_position_cost(sandbox._order_history)
        if open_cost > budget:
            raise ValueError(f"当前在持总成本 ¥{open_cost:.2f} 超过初始资金 ¥{budget:.2f}，请提高初始资金后重试")
        sandbox._initial_cash = budget
        sandbox._cash_available = self._round_cash(budget - open_cost)

        with self._lock:
            # 同一事务提交初始资金、生息调整与订单快照，失败时不污染内存
            if self._state_store is not None:
                self._state_store.replace_orders(
                    sandbox._order_history,
                    scope=self._scope,
                    initial_cash=budget,
                    interest_adjust_g=interest_diff,
                )
            self._initial_cash = sandbox._initial_cash
            self._cash_available = sandbox._cash_available
            self._cash_frozen = Decimal("0.0")
            self._gold_total_g = sandbox._gold_total_g
            self._gold_available_g = sandbox._gold_available_g
            self._gold_frozen_g = Decimal("0.0")
            self._cost_basis_per_g = sandbox._cost_basis_per_g
            self._realized_pnl = sandbox._realized_pnl
            self._unrealized_pnl = Decimal("0.0")
            self._interest_pending_g = Decimal("0.0")
            self._interest_credited_g = sandbox._interest_credited_g
            self._order_history = list(sandbox._order_history)
            self._orders_by_id = dict(sandbox._orders_by_id)
            self._realized_events = list(sandbox._realized_events)
            return self.take_snapshot(now_dt)

    def compute_period_pnl(self, as_of: datetime, current_bid_price: float) -> tuple[float, float]:
        """核算指定时点的当日累计盈亏与近 7 日累计盈亏（已实现盈亏 + 当前浮动盈亏）。"""
        if not isfinite(current_bid_price) or current_bid_price < 0:
            raise ValueError("重估价格必须为非负有限数")
        as_of_utc = as_of.astimezone(UTC) if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
        shanghai_tz = ZoneInfo("Asia/Shanghai")
        day_start = as_of_utc.astimezone(shanghai_tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        week_start = as_of_utc - timedelta(days=7)
        with self._lock:
            unrealized = self.mark_to_market(current_bid_price)
            daily_realized = sum(
                (pnl for event_dt, pnl in self._realized_events if day_start <= event_dt <= as_of_utc),
                Decimal("0.0"),
            )
            weekly_realized = sum(
                (pnl for event_dt, pnl in self._realized_events if week_start <= event_dt <= as_of_utc),
                Decimal("0.0"),
            )
            return float(self._round_cash(daily_realized + unrealized)), float(
                self._round_cash(weekly_realized + unrealized)
            )

    def compute_fifo_lots(
        self,
        reference_price: float | None = None,
        *,
        only_open: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> FifoSummaryResponse:
        """按先进先出（FIFO）撮合全部历史订单，核算在持批次、剩余克重及当前浮盈。"""
        if offset < 0:
            raise ValueError("offset 不能为负数")
        if limit is not None and limit < 0:
            raise ValueError("limit 不能为负数")

        with self._lock:
            orders = list(self._order_history)

        buy_lots: list[tuple[int, Decimal, Decimal, Decimal | None, Decimal]] = []
        enriched: list[FifoLotItem] = []
        head = 0

        for idx, order in enumerate(orders):
            is_filled = order.filled_grams is not None and order.filled_grams > 0
            initial_status: Literal["HOLDING", "PARTIAL", "CLOSED", "SELL", "UNFILLED"] = (
                "UNFILLED" if not is_filled else ("HOLDING" if order.direction == TradeDirection.BUY else "SELL")
            )

            ts_val = order.bank_event_at or order.received_at
            ts_str = ts_val.isoformat() if ts_val is not None else None

            enriched.append(
                FifoLotItem(
                    client_order_id=order.client_order_id,
                    bank_order_id=order.bank_order_id,
                    direction=order.direction,
                    lot_status=initial_status,
                    requested_grams=float(order.requested_grams),
                    filled_grams=float(order.filled_grams) if order.filled_grams is not None else None,
                    remaining_grams=0.0,
                    execution_price=float(order.execution_price) if order.execution_price is not None else None,
                    fee=float(order.fee) if order.fee is not None else None,
                    lot_pnl=None,
                    timestamp=ts_str,
                )
            )

            if not is_filled or order.filled_grams is None:
                continue

            filled_g = self._round_gold(Decimal(str(order.filled_grams)))
            if order.direction == TradeDirection.BUY:
                exec_price = Decimal(str(order.execution_price)) if order.execution_price is not None else None
                buy_fee = self._round_cash(Decimal(str(order.fee or 0.0)))
                buy_lots.append((idx, filled_g, filled_g, exec_price, buy_fee))
            elif order.direction == TradeDirection.SELL:
                need = filled_g
                while head < len(buy_lots) and need > Decimal("0.0"):
                    lot_idx, lot_filled, lot_rem, lot_price, lot_fee = buy_lots[head]
                    if lot_rem <= Decimal("0.0"):
                        head += 1
                        continue
                    take = min(lot_rem, need)
                    new_rem = self._round_gold(lot_rem - take)
                    if new_rem <= Decimal("0.0"):
                        new_rem = Decimal("0.0")
                        buy_lots[head] = (lot_idx, lot_filled, new_rem, lot_price, lot_fee)
                        head += 1
                    else:
                        buy_lots[head] = (lot_idx, lot_filled, new_rem, lot_price, lot_fee)
                    need = self._round_gold(need - take)
                    if need <= Decimal("0.0"):
                        break

        open_cost = Decimal("0.0")
        open_g = Decimal("0.0")
        open_lot_count = 0
        ref_dec = (
            Decimal(str(reference_price))
            if reference_price is not None and isfinite(reference_price) and reference_price > 0
            else None
        )

        for lot_idx, lot_filled, lot_rem, lot_price, lot_fee in buy_lots:
            if lot_rem <= Decimal("0.0"):
                status: Literal["HOLDING", "PARTIAL", "CLOSED", "SELL", "UNFILLED"] = "CLOSED"
            elif lot_rem < lot_filled:
                status = "PARTIAL"
            else:
                status = "HOLDING"

            lot_pnl: float | None = None
            if lot_rem > Decimal("0.0"):
                open_lot_count += 1
                open_g += lot_rem
                if lot_price is not None:
                    alloc_fee = (lot_fee * lot_rem / lot_filled) if lot_filled > Decimal("0.0") else Decimal("0.0")
                    lot_cost = lot_rem * lot_price + alloc_fee
                    open_cost += lot_cost
                    if ref_dec is not None:
                        lot_pnl = float(self._round_cash(lot_rem * ref_dec - lot_cost))

            enriched[lot_idx] = enriched[lot_idx].model_copy(
                update={
                    "remaining_grams": float(lot_rem),
                    "lot_status": status,
                    "lot_pnl": lot_pnl,
                }
            )

        reversed_items = list(reversed(enriched))
        filtered = (
            [item for item in reversed_items if item.lot_status in ("HOLDING", "PARTIAL")]
            if only_open
            else reversed_items
        )

        if limit == 0:
            paged: list[FifoLotItem] = []
        elif limit is not None:
            paged = filtered[offset : offset + limit]
        else:
            paged = filtered[offset:]

        return FifoSummaryResponse(
            open_position_cost=float(self._round_cash(open_cost)),
            open_grams=float(self._round_gold(open_g)),
            open_lot_count=open_lot_count,
            total_orders=len(enriched),
            items=paged,
        )
