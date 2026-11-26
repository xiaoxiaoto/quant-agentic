"""程序化执行适配器与挂单机制服务。

支持程序化订单生命周期管理（CREATED -> SUBMITTED -> ACKNOWLEDGED -> FILLED/REJECTED/CANCELED/UNKNOWN）、
幂等订单追踪映射、非交易时段批量价挂单模拟与人工急停。
当前不连接银行，也不预留资金或持仓。
"""

from collections.abc import Callable
from datetime import UTC, datetime
from math import isfinite
from threading import RLock

from src.contracts.domains.account import OrderEvent
from src.contracts.domains.execution import BatchOrderInstruction, RFQFillResult
from src.contracts.enums.trading import OrderState, TradeDirection
from src.repositories.runtime_state import RuntimeStateRepository
from src.services.execution.shadow_ledger import ShadowLedger


class ExecutionAdapter:
    """程序化交易执行与订单生命周期适配器。"""

    def __init__(
        self,
        ledger: ShadowLedger | None = None,
        is_live_enabled: bool = False,
        state_store: RuntimeStateRepository | None = None,
        *,
        kill_switch_checker: Callable[[], bool] | None = None,
    ) -> None:
        self._lock = RLock()
        self._state_store = state_store
        self._ledger = ledger or ShadowLedger()
        self._live_enabled = is_live_enabled
        self._kill_switch_active = False
        self._kill_switch_checker = kill_switch_checker
        self._orders: dict[str, OrderEvent] = {}
        self._id_mapping: dict[str, str] = {}  # bank_order_id -> client_order_id
        self._fills: dict[str, RFQFillResult] = {}
        self._batch_instructions: dict[str, BatchOrderInstruction] = {}
        if self._state_store is not None:
            _, saved_kill = self._state_store.load_control_state()
            self._kill_switch_active = saved_kill
            for inst in self._state_store.load_batch_instructions():
                self._batch_instructions[inst.instruction_id] = inst

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _persist_batch_instructions(self) -> None:
        if self._state_store is not None:
            self._state_store.save_batch_instructions(list(self._batch_instructions.values()))

    def _persist_kill_switch(self) -> None:
        if self._state_store is not None:
            self._state_store.save_kill_switch_state(self._kill_switch_active)

    def trigger_kill_switch(self, reason: str = "MANUAL_EMERGENCY_HALT") -> None:
        """触发人工急停开关，强行阻断所有新报单。"""
        with self._lock:
            prev = self._kill_switch_active
            self._kill_switch_active = True
            try:
                self._persist_kill_switch()
            except Exception:
                self._kill_switch_active = prev
                raise

    def reset_kill_switch(self) -> None:
        """解除急停状态，恢复正常报单链路。"""
        with self._lock:
            prev = self._kill_switch_active
            self._kill_switch_active = False
            try:
                self._persist_kill_switch()
            except Exception:
                self._kill_switch_active = prev
                raise

    def is_kill_switch_active(self) -> bool:
        """检查急停开关当前是否激活。"""
        with self._lock:
            if self._kill_switch_checker is not None:
                return self._kill_switch_active or self._kill_switch_checker()
            return self._kill_switch_active

    def submit_order(
        self,
        client_order_id: str,
        direction: TradeDirection,
        grams: float,
        limit_price: float | None = None,
    ) -> OrderEvent:
        """报送一笔交易委托并完成前置幂等检查与状态初始化。"""
        with self._lock:
            if self.is_kill_switch_active():
                raise RuntimeError("系统处于人工急停状态，严禁提交任何新订单")

            if client_order_id in self._orders:
                # 幂等拦截：已存在的订单直接返回当前状态
                existing = self._orders[client_order_id]
                if (existing.direction, existing.requested_grams, existing.limit_price) != (
                    direction,
                    grams,
                    limit_price,
                ):
                    raise ValueError("同一订单号的委托内容冲突")
                return existing.model_copy(deep=True)

            if not isfinite(grams) or grams <= 0:
                raise ValueError("委托克重必须为正且有限")
            if limit_price is not None and (not isfinite(limit_price) or limit_price <= 0):
                raise ValueError("委托限价必须为正且有限")

            now_dt = self._now()
            order = OrderEvent(
                client_order_id=client_order_id,
                order_type="LIMIT" if limit_price is not None else "MARKET",
                direction=direction,
                requested_grams=grams,
                limit_price=limit_price,
                state=OrderState.CREATED,
                received_at=now_dt,
            )
            self._orders[client_order_id] = order

            # 模拟进入银行受理状态
            order.state = OrderState.SUBMITTED
            return order.model_copy(deep=True)

    def acknowledge_order(
        self,
        client_order_id: str,
        bank_order_id: str,
    ) -> OrderEvent:
        """记录银行官方受理应答，绑定订单号映射。"""
        with self._lock:
            order = self._orders.get(client_order_id)
            if not order:
                raise KeyError(f"未找到客户订单：{client_order_id}")

            if order.state not in (OrderState.SUBMITTED, OrderState.ACKNOWLEDGED):
                raise ValueError("终态订单不得重新受理")
            if bank_order_id in self._id_mapping and self._id_mapping[bank_order_id] != client_order_id:
                raise ValueError("银行订单号已绑定其他委托")
            if order.bank_order_id is not None and order.bank_order_id != bank_order_id:
                raise ValueError("订单已绑定不同银行订单号")
            order.bank_order_id = bank_order_id
            order.state = OrderState.ACKNOWLEDGED
            self._id_mapping[bank_order_id] = client_order_id
            return order.model_copy(deep=True)

    def process_fill(
        self,
        client_order_id: str,
        fill_result: RFQFillResult,
    ) -> OrderEvent:
        """处理撮合成交回报，同步写入影子账本。"""
        with self._lock:
            order = self._orders.get(client_order_id)
            if not order:
                raise KeyError(f"未找到客户订单：{client_order_id}")

            if client_order_id in self._fills:
                if self._fills[client_order_id] != fill_result:
                    raise ValueError("终态订单收到冲突成交回报")
                return order.model_copy(deep=True)
            if order.state in (OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELED):
                raise ValueError("终态订单不得再次成交")

            now_dt = self._now()
            if not fill_result.is_filled or fill_result.filled_price is None:
                order.state = OrderState.REJECTED
                self._fills[client_order_id] = fill_result.model_copy(deep=True)
                return order.model_copy(deep=True)

            if fill_result.filled_grams != order.requested_grams:
                raise ValueError("成交克重必须与全量委托一致")
            event_at = fill_result.filled_at or now_dt

            # 同步记录至本地影子账本
            if order.direction == TradeDirection.BUY:
                self._ledger.record_buy(
                    client_order_id=client_order_id,
                    grams=fill_result.filled_grams,
                    price=fill_result.filled_price,
                    fee=fill_result.fee_cny,
                    bank_order_id=order.bank_order_id,
                    timestamp=event_at,
                )
            else:
                self._ledger.record_sell(
                    client_order_id=client_order_id,
                    grams=fill_result.filled_grams,
                    price=fill_result.filled_price,
                    fee=fill_result.fee_cny,
                    bank_order_id=order.bank_order_id,
                    timestamp=event_at,
                )

            order.state = OrderState.FILLED
            order.filled_grams = fill_result.filled_grams
            order.execution_price = fill_result.filled_price
            order.fee = fill_result.fee_cny
            order.bank_event_at = fill_result.filled_at or now_dt
            self._fills[client_order_id] = fill_result.model_copy(deep=True)
            return order.model_copy(deep=True)

    def query_order(self, client_order_id: str) -> OrderEvent | None:
        """查单确认接口，针对 UNKNOWN 状态订单进行核实。"""
        with self._lock:
            order = self._orders.get(client_order_id)
            return order.model_copy(deep=True) if order is not None else None

    def register_batch_instruction(
        self,
        instruction_id: str,
        direction: TradeDirection,
        grams: float,
        limit_price: float,
        target_open: datetime,
    ) -> BatchOrderInstruction:
        """在非交易时段登记次开盘批量价挂单指令。"""
        with self._lock:
            if self.is_kill_switch_active():
                raise RuntimeError("系统已触发 Kill-Switch 熔断，拒绝登记批量挂单指令！")
            if not isfinite(grams) or grams <= 0:
                raise ValueError("批量挂单克重必须为正且有限")
            if not isfinite(limit_price) or limit_price <= 0:
                raise ValueError("批量挂单限价必须为正且有限")
            existing = self._batch_instructions.get(instruction_id)
            if existing is not None:
                if (
                    existing.direction != direction
                    or existing.requested_grams != grams
                    or existing.limit_price != limit_price
                ):
                    raise ValueError(f"批量挂单指令 ID 冲突：{instruction_id}")
                return existing.model_copy(deep=True)
            inst = BatchOrderInstruction(
                instruction_id=instruction_id,
                direction=direction,
                requested_grams=grams,
                limit_price=limit_price,
                placed_at=self._now(),
                target_session_open=target_open,
            )
            self._batch_instructions[instruction_id] = inst
            try:
                self._persist_batch_instructions()
            except Exception:
                self._batch_instructions.pop(instruction_id, None)
                raise
            return inst.model_copy(deep=True)

    def execute_batch_at_open(
        self,
        instruction_id: str,
        bank_batch_price: float,
    ) -> tuple[bool, BatchOrderInstruction]:
        """次开盘依据银行公布的批量价格核算挂单是否撮合成交。

        买单需 limit_price >= batch_price 才可成交；
        卖单需 limit_price <= batch_price 才可成交。
        """
        with self._lock:
            if self.is_kill_switch_active():
                raise RuntimeError("系统已触发 Kill-Switch 熔断，拒绝执行批量挂单指令！")
            if not isfinite(bank_batch_price) or bank_batch_price <= 0:
                raise ValueError("银行批量成交价必须为正且有限")
            inst = self._batch_instructions.get(instruction_id)
            if not inst:
                raise KeyError(f"未找到挂单指令：{instruction_id}")
            if inst.is_executed:
                raise ValueError(f"批量挂单指令已执行，不可重复撮合：{instruction_id}")

            inst.batch_price = bank_batch_price
            can_fill = False

            if inst.direction == TradeDirection.BUY:
                can_fill = inst.limit_price >= bank_batch_price
            else:
                can_fill = inst.limit_price <= bank_batch_price

            inst.is_executed = can_fill
            self._persist_batch_instructions()
            return can_fill, inst.model_copy(deep=True)
