"""运行时状态 SQLite 规范表持久化仓储。

与京东授权及行情表共享 DATA_DIR/quant-agentic.sqlite3（WAL 模式），
将影子账本订单、合规交易提醒、批量挂单指令、风控急停与冷却状态、
新闻与宏观风控事件以及对账报告拆分为独立规范表，消除整包 JSON 覆写与重启丢态问题。
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import Text, column, delete, desc, func, inspect, select, table, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from src.contracts.domains.account import OrderEvent, ReconciliationReport
from src.contracts.domains.alert import AlertPayload
from src.contracts.domains.event import MacroCalendarEvent, NewsEvent
from src.contracts.domains.execution import BatchOrderInstruction
from src.contracts.enums.trading import RiskState
from src.repositories.database import (
    AlertRecord,
    BatchInstructionRecord,
    ControlStateRecord,
    LedgerMetaRecord,
    LedgerOrderRecord,
    MacroEventRecord,
    NewsEventRecord,
    ReconciliationReportRecord,
    SQLiteDatabase,
)


class RuntimeStateRepository:
    """基于 SQLite 规范表的运行时状态持久化仓储。"""

    _DEFAULT_SCOPE: str = "default"
    _LEGACY_KEY_LEDGER_ORDERS: str = "ledger_orders"
    _LEGACY_KEY_ALERTS: str = "alerts"
    _LEGACY_KEY_BATCH_INSTRUCTIONS: str = "batch_instructions"
    _LEGACY_KEY_CONTROL_STATE: str = "control_state"

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._engine = SQLiteDatabase.create_engine_for_path(self._path)
        self._migrate_ledger_scope_column()
        self._migrate_legacy_kv_state()

    @staticmethod
    def _legacy_order_scope(order: OrderEvent) -> str:
        """按历史订单号前缀推断所属账本范围（本地模拟盘订单以 paper_ 开头）。"""
        return "paper" if order.client_order_id.startswith("paper_") else "live"

    def _migrate_ledger_scope_column(self) -> None:
        """为旧版订单表补充账本范围列，并将历史本地模拟盘订单迁出实盘辅助账本。"""
        with self._engine.begin() as connection:
            columns = {item["name"] for item in inspect(connection).get_columns(LedgerOrderRecord.__tablename__)}
            if "scope" in columns:
                return
            connection.execute(text("ALTER TABLE ledger_orders ADD COLUMN scope TEXT NOT NULL DEFAULT 'live'"))
            connection.execute(
                text("UPDATE ledger_orders SET scope = 'paper' WHERE client_order_id LIKE 'paper\\_%' ESCAPE '\\'")
            )

    @staticmethod
    def _format_utc(dt: datetime) -> str:
        utc_dt = dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @staticmethod
    def _parse_utc(raw: str | None) -> datetime | None:
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    def _migrate_legacy_kv_state(self) -> None:
        """若旧版 runtime_state KV 表存在且对应规范表为空，则自动平滑迁移数据。"""
        with self._engine.connect() as connection:
            if not inspect(connection).has_table("runtime_state"):
                return
            legacy = table("runtime_state", column("key", Text), column("payload", Text))
            kv_rows: dict[str, str] = {
                str(row[0]): str(row[1]) for row in connection.execute(select(legacy.c.key, legacy.c.payload)).all()
            }

        if not kv_rows:
            return

        with Session(self._engine) as session, session.begin():
            if (
                self._LEGACY_KEY_LEDGER_ORDERS in kv_rows
                and session.execute(select(func.count()).select_from(LedgerOrderRecord)).scalar_one() == 0
            ):
                items = json.loads(kv_rows[self._LEGACY_KEY_LEDGER_ORDERS])
                for item in items:
                    order = OrderEvent.model_validate(item)
                    self._upsert_order_in_session(session, order, scope=self._legacy_order_scope(order))

            if (
                self._LEGACY_KEY_ALERTS in kv_rows
                and session.execute(select(func.count()).select_from(AlertRecord)).scalar_one() == 0
            ):
                items = json.loads(kv_rows[self._LEGACY_KEY_ALERTS])
                for item in items:
                    self._upsert_alert_in_session(session, AlertPayload.model_validate(item))

            if (
                self._LEGACY_KEY_BATCH_INSTRUCTIONS in kv_rows
                and session.execute(select(func.count()).select_from(BatchInstructionRecord)).scalar_one() == 0
            ):
                items = json.loads(kv_rows[self._LEGACY_KEY_BATCH_INSTRUCTIONS])
                for item in items:
                    self._upsert_batch_in_session(session, BatchOrderInstruction.model_validate(item))

            if (
                self._LEGACY_KEY_CONTROL_STATE in kv_rows
                and session.get(ControlStateRecord, self._DEFAULT_SCOPE) is None
            ):
                data = json.loads(kv_rows[self._LEGACY_KEY_CONTROL_STATE])
                risk_state = RiskState(data.get("risk_state", RiskState.NORMAL.value))
                kill_switch = bool(data.get("kill_switch_active", False))
                self._save_control_in_session(session, risk_state, kill_switch, None)

    def _upsert_order_in_session(self, session: Session, order: OrderEvent, *, scope: str = "live") -> None:
        raw_json = json.dumps(order.model_dump(mode="json"), ensure_ascii=False)
        stmt = insert(LedgerOrderRecord).values(
            scope=scope,
            client_order_id=order.client_order_id,
            bank_order_id=order.bank_order_id,
            order_type=order.order_type,
            direction=order.direction.value,
            requested_grams=order.requested_grams,
            filled_grams=order.filled_grams,
            execution_price=order.execution_price,
            fee=order.fee,
            state=order.state.value,
            bank_event_at=self._format_utc(order.bank_event_at) if order.bank_event_at is not None else None,
            received_at=self._format_utc(order.received_at or datetime.now(UTC)),
            raw_json=raw_json,
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[LedgerOrderRecord.client_order_id],
                set_={
                    "bank_order_id": stmt.excluded.bank_order_id,
                    "filled_grams": stmt.excluded.filled_grams,
                    "execution_price": stmt.excluded.execution_price,
                    "fee": stmt.excluded.fee,
                    "state": stmt.excluded.state,
                    "bank_event_at": stmt.excluded.bank_event_at,
                    "received_at": stmt.excluded.received_at,
                    "raw_json": stmt.excluded.raw_json,
                },
            )
        )

    def _upsert_alert_in_session(self, session: Session, alert: AlertPayload) -> None:
        raw_json = json.dumps(alert.model_dump(mode="json"), ensure_ascii=False)
        stmt = insert(AlertRecord).values(
            alert_id=alert.alert_id,
            decision_id=alert.decision_id,
            created_at=self._format_utc(alert.created_at),
            expires_at=self._format_utc(alert.expires_at),
            action=alert.action.value,
            state=alert.state.value,
            raw_json=raw_json,
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[AlertRecord.alert_id],
                set_={
                    "expires_at": stmt.excluded.expires_at,
                    "state": stmt.excluded.state,
                    "raw_json": stmt.excluded.raw_json,
                },
            )
        )

    def _upsert_batch_in_session(self, session: Session, instruction: BatchOrderInstruction) -> None:
        raw_json = json.dumps(instruction.model_dump(mode="json"), ensure_ascii=False)
        stmt = insert(BatchInstructionRecord).values(
            instruction_id=instruction.instruction_id,
            direction=instruction.direction.value,
            target_open=self._format_utc(instruction.target_session_open),
            state="EXECUTED" if instruction.is_executed else "PENDING",
            raw_json=raw_json,
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[BatchInstructionRecord.instruction_id],
                set_={
                    "state": stmt.excluded.state,
                    "raw_json": stmt.excluded.raw_json,
                },
            )
        )

    def _save_control_in_session(
        self,
        session: Session,
        risk_state: RiskState,
        kill_switch_active: bool,
        last_signal_at: datetime | None,
    ) -> None:
        existing = session.get(ControlStateRecord, self._DEFAULT_SCOPE)
        effective_last_signal = (
            self._format_utc(last_signal_at)
            if last_signal_at is not None
            else (existing.last_signal_at if existing is not None else None)
        )
        stmt = insert(ControlStateRecord).values(
            scope=self._DEFAULT_SCOPE,
            risk_state=risk_state.value,
            kill_switch_active=kill_switch_active,
            last_signal_at=effective_last_signal,
            updated_at=self._format_utc(datetime.now(UTC)),
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[ControlStateRecord.scope],
                set_={
                    "risk_state": stmt.excluded.risk_state,
                    "kill_switch_active": stmt.excluded.kill_switch_active,
                    "last_signal_at": stmt.excluded.last_signal_at,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )

    def load_orders(self, *, scope: str = "live") -> list[OrderEvent]:
        """按插入顺序读取指定账本范围内持久化的影子账本成交订单列表。"""
        with Session(self._engine) as session:
            rows = (
                session.execute(
                    select(LedgerOrderRecord.raw_json)
                    .where(LedgerOrderRecord.scope == scope)
                    .order_by(LedgerOrderRecord.seq)
                )
                .scalars()
                .all()
            )
        try:
            return [OrderEvent.model_validate_json(raw) for raw in rows]
        except ValidationError as exc:
            raise ValueError("持久化影子账本订单数据损坏") from exc

    def upsert_order(self, order: OrderEvent, *, scope: str = "live") -> None:
        """增量写入或更新单笔影子账本订单记录。"""
        with Session(self._engine) as session, session.begin():
            self._upsert_order_in_session(session, order, scope=scope)

    def save_orders(self, orders: list[OrderEvent], *, scope: str = "live") -> None:
        """批量增量持久化影子账本成交订单列表。"""
        with Session(self._engine) as session, session.begin():
            for order in orders:
                self._upsert_order_in_session(session, order, scope=scope)

    def clear_orders(self, *, scope: str = "live") -> None:
        """清空指定账本范围内持久化的影子账本订单记录。"""
        with Session(self._engine) as session, session.begin():
            session.execute(delete(LedgerOrderRecord).where(LedgerOrderRecord.scope == scope))

    def replace_orders(
        self,
        orders: list[OrderEvent],
        *,
        scope: str = "live",
        initial_cash: Decimal,
        interest_adjust_g: Decimal,
    ) -> None:
        """在同一事务中重写指定账本范围的全部订单与初始化元数据，确保重置操作原子性。"""
        with Session(self._engine) as session, session.begin():
            session.execute(delete(LedgerOrderRecord).where(LedgerOrderRecord.scope == scope))
            for order in orders:
                self._upsert_order_in_session(session, order, scope=scope)
            stmt = insert(LedgerMetaRecord).values(
                scope=scope,
                initial_cash=str(initial_cash),
                interest_adjust_g=str(interest_adjust_g),
                updated_at=self._format_utc(datetime.now(UTC)),
            )
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[LedgerMetaRecord.scope],
                    set_={
                        "initial_cash": stmt.excluded.initial_cash,
                        "interest_adjust_g": stmt.excluded.interest_adjust_g,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
            )

    def load_ledger_meta(self, *, scope: str = "live") -> tuple[Decimal, Decimal] | None:
        """读取指定账本范围的初始化元数据（初始资金, 生息补齐克重）；从未初始化时返回 None。"""
        with Session(self._engine) as session:
            row = session.get(LedgerMetaRecord, scope)
            if row is None:
                return None
            return Decimal(row.initial_cash), Decimal(row.interest_adjust_g)

    def load_alerts(self) -> list[AlertPayload]:
        """读取持久化的交易提醒列表。"""
        with Session(self._engine) as session:
            rows = session.execute(select(AlertRecord.raw_json).order_by(AlertRecord.created_at)).scalars().all()
        try:
            return [AlertPayload.model_validate_json(raw) for raw in rows]
        except ValidationError as exc:
            raise ValueError("持久化交易提醒数据损坏") from exc

    def upsert_alert(self, alert: AlertPayload) -> None:
        """增量写入或更新单条交易提醒。"""
        with Session(self._engine) as session, session.begin():
            self._upsert_alert_in_session(session, alert)

    def save_alerts(self, alerts: list[AlertPayload]) -> None:
        """批量增量持久化交易提醒列表。"""
        with Session(self._engine) as session, session.begin():
            for alert in alerts:
                self._upsert_alert_in_session(session, alert)

    def load_batch_instructions(self) -> list[BatchOrderInstruction]:
        """读取持久化的非交易时段批量挂单指令列表。"""
        with Session(self._engine) as session:
            rows = session.execute(select(BatchInstructionRecord.raw_json)).scalars().all()
        try:
            return [BatchOrderInstruction.model_validate_json(raw) for raw in rows]
        except ValidationError as exc:
            raise ValueError("持久化批量挂单指令数据损坏") from exc

    def upsert_batch_instruction(self, instruction: BatchOrderInstruction) -> None:
        """增量写入或更新单条非交易时段批量挂单指令。"""
        with Session(self._engine) as session, session.begin():
            self._upsert_batch_in_session(session, instruction)

    def save_batch_instructions(self, instructions: list[BatchOrderInstruction]) -> None:
        """批量持久化非交易时段批量挂单指令列表。"""
        with Session(self._engine) as session, session.begin():
            for instruction in instructions:
                self._upsert_batch_in_session(session, instruction)

    def load_control_state(self) -> tuple[RiskState, bool]:
        """读取风控状态与 Kill-Switch 急停标志。"""
        with Session(self._engine) as session:
            row = session.get(ControlStateRecord, self._DEFAULT_SCOPE)
        if row is None:
            return RiskState.NORMAL, False
        return RiskState(row.risk_state), bool(row.kill_switch_active)

    def save_control_state(
        self,
        risk_state: RiskState,
        kill_switch_active: bool,
        *,
        last_signal_at: datetime | None = None,
    ) -> None:
        """持久化风控状态、Kill-Switch 急停标志与可选冷却时间。"""
        with Session(self._engine) as session, session.begin():
            self._save_control_in_session(session, risk_state, kill_switch_active, last_signal_at)

    def load_last_signal_at(self) -> datetime | None:
        """读取最近一次交易信号触发时间（用于信号冷却期控制）。"""
        with Session(self._engine) as session:
            row = session.get(ControlStateRecord, self._DEFAULT_SCOPE)
        return self._parse_utc(row.last_signal_at) if row is not None else None

    def save_last_signal_at(self, signal_at: datetime) -> None:
        """更新最近一次交易信号触发时间，保留当前风控与急停状态。"""
        with Session(self._engine) as session, session.begin():
            existing = session.get(ControlStateRecord, self._DEFAULT_SCOPE)
            risk_state = RiskState(existing.risk_state) if existing is not None else RiskState.NORMAL
            kill_switch = bool(existing.kill_switch_active) if existing is not None else False
            self._save_control_in_session(session, risk_state, kill_switch, signal_at)

    def load_news_events(self) -> list[NewsEvent]:
        """读取持久化的新闻风控事件列表。"""
        with Session(self._engine) as session:
            rows = (
                session.execute(select(NewsEventRecord.raw_json).order_by(NewsEventRecord.published_at)).scalars().all()
            )
        try:
            return [NewsEvent.model_validate_json(raw) for raw in rows]
        except ValidationError as exc:
            raise ValueError("持久化新闻风控事件数据损坏") from exc

    def upsert_news_event(self, event: NewsEvent) -> None:
        """增量持久化单条新闻风控事件。"""
        raw_json = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        stmt = insert(NewsEventRecord).values(
            event_id=event.event_id,
            published_at=self._format_utc(event.published_at),
            available_at=self._format_utc(event.available_at) if event.available_at is not None else None,
            severity=event.severity,
            confidence=event.confidence,
            raw_json=raw_json,
        )
        with Session(self._engine) as session, session.begin():
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[NewsEventRecord.event_id],
                    set_={
                        "available_at": stmt.excluded.available_at,
                        "severity": stmt.excluded.severity,
                        "confidence": stmt.excluded.confidence,
                        "raw_json": stmt.excluded.raw_json,
                    },
                )
            )

    def load_macro_events(self) -> list[MacroCalendarEvent]:
        """读取持久化的宏观日程锁定事件列表。"""
        with Session(self._engine) as session:
            rows = (
                session.execute(select(MacroEventRecord.raw_json).order_by(MacroEventRecord.scheduled_at))
                .scalars()
                .all()
            )
        try:
            return [MacroCalendarEvent.model_validate_json(raw) for raw in rows]
        except ValidationError as exc:
            raise ValueError("持久化宏观日程事件数据损坏") from exc

    def upsert_macro_event(self, event: MacroCalendarEvent) -> None:
        """增量持久化单条宏观日程锁定事件。"""
        raw_json = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        stmt = insert(MacroEventRecord).values(
            event_id=event.event_id,
            scheduled_at=self._format_utc(event.scheduled_at),
            raw_json=raw_json,
        )
        with Session(self._engine) as session, session.begin():
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[MacroEventRecord.event_id],
                    set_={
                        "scheduled_at": stmt.excluded.scheduled_at,
                        "raw_json": stmt.excluded.raw_json,
                    },
                )
            )

    def save_reconciliation_report(self, report: ReconciliationReport) -> None:
        """持久化对账报告审计记录。"""
        raw_json = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
        stmt = insert(ReconciliationReportRecord).values(
            report_id=report.report_id,
            as_of=self._format_utc(report.as_of),
            matched=report.matched,
            summary=report.summary,
            raw_json=raw_json,
        )
        with Session(self._engine) as session, session.begin():
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[ReconciliationReportRecord.report_id],
                    set_={
                        "matched": stmt.excluded.matched,
                        "summary": stmt.excluded.summary,
                        "raw_json": stmt.excluded.raw_json,
                    },
                )
            )

    def load_latest_reconciliation_report(self) -> ReconciliationReport | None:
        """读取最近一次持久化的对账报告。"""
        with Session(self._engine) as session:
            raw = session.execute(
                select(ReconciliationReportRecord.raw_json)
                .order_by(desc(ReconciliationReportRecord.as_of), desc(ReconciliationReportRecord.id))
                .limit(1)
            ).scalar_one_or_none()
        if raw is None:
            return None
        return ReconciliationReport.model_validate_json(raw)

    def list_reconciliation_reports(self, limit: int = 50) -> list[ReconciliationReport]:
        """按时间倒序查询历史对账审计报告列表。"""
        with Session(self._engine) as session:
            rows = (
                session.execute(
                    select(ReconciliationReportRecord.raw_json)
                    .order_by(desc(ReconciliationReportRecord.as_of), desc(ReconciliationReportRecord.id))
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        return [ReconciliationReport.model_validate_json(raw) for raw in rows]
