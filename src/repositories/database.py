"""统一 SQLite（WAL 模式）数据库引擎与核心业务 ORM 表定义。

集中管理行情采集表与运行时状态规范表：
- quotes：银行/合成双边 PIT 行情表
- reference_quotes：多品种实时参考行情采集表
- ledger_orders：影子账本成交订单表（按 scope 区分实盘辅助与本地模拟盘）
- ledger_meta：影子账本初始化元数据表
- alerts：实盘合规交易提醒表
- batch_instructions：非交易时段批量挂单指令表
- control_state：风控状态、人工急停与信号冷却时间表
- news_events：新闻风控事件表
- macro_events：宏观日程锁定事件表
- reconciliation_reports：对账报告审计表
"""

import os
import sqlite3
from pathlib import Path
from threading import Lock
from typing import ClassVar

from sqlalchemy import URL, Boolean, Engine, Float, Index, Integer, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool


class QuantBase(DeclarativeBase):
    """quant-agentic SQLite 业务与采集表声明式基类。"""


class QuoteRecord(QuantBase):
    """银行/模拟双边 PIT 行情表。"""

    __tablename__ = "quotes"
    __table_args__ = (
        Index("ix_quotes_product_pit", "product_code", "quality_state", "pit_cutoff_utc", "quote_at_utc"),
        Index("ix_quotes_product_source_time", "product_code", "source_id", "quote_at_utc"),
        Index(
            "ix_quotes_product_quality_time_id",
            "product_code",
            "quality_state",
            "quote_at_utc",
            "id",
            "pit_cutoff_utc",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quote_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    product_code: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    bid_price: Mapped[float] = mapped_column(Float, nullable=False)
    ask_price: Mapped[float] = mapped_column(Float, nullable=False)
    quote_at_utc: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    received_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
    available_at_utc: Mapped[str | None] = mapped_column(Text, nullable=True)
    pit_cutoff_utc: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    is_tradable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    response_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    record_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_state: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)


class ReferenceQuoteRecord(QuantBase):
    """多品种实时参考行情采集表。"""

    __tablename__ = "reference_quotes"
    __table_args__ = (
        Index("ix_ref_quotes_product_id", "product_code", "id"),
        Index("ix_ref_quotes_product_received", "product_code", "received_at_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quote_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    product_code: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    price_side: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    quote_at_utc: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    received_at_utc: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    available_at_utc: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_tradable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quality_state: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    record_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)


class LedgerOrderRecord(QuantBase):
    """影子账本成交订单明细表。"""

    __tablename__ = "ledger_orders"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="live", server_default="live")
    client_order_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    bank_order_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    requested_grams: Mapped[float] = mapped_column(Float, nullable=False)
    filled_grams: Mapped[float | None] = mapped_column(Float, nullable=True)
    execution_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    bank_event_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)


class LedgerMetaRecord(QuantBase):
    """影子账本初始化元数据表（初始资金与生息补齐克重），与订单快照同事务提交。"""

    __tablename__ = "ledger_meta"

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    initial_cash: Mapped[str] = mapped_column(Text, nullable=False)
    interest_adjust_g: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class AlertRecord(QuantBase):
    """实盘合规交易提醒表。"""

    __tablename__ = "alerts"

    alert_id: Mapped[str] = mapped_column(Text, primary_key=True)
    decision_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)


class BatchInstructionRecord(QuantBase):
    """非交易时段次开盘批量挂单指令表。"""

    __tablename__ = "batch_instructions"

    instruction_id: Mapped[str] = mapped_column(Text, primary_key=True)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    target_open: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)


class ControlStateRecord(QuantBase):
    """风控状态、人工急停开关与冷却时间表。"""

    __tablename__ = "control_state"

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    risk_state: Mapped[str] = mapped_column(Text, nullable=False)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_signal_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class NewsEventRecord(QuantBase):
    """新闻风控事件持久化表。"""

    __tablename__ = "news_events"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    published_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    available_at: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    severity: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)


class MacroEventRecord(QuantBase):
    """宏观日程锁定事件持久化表。"""

    __tablename__ = "macro_events"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    scheduled_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)


class ReconciliationReportRecord(QuantBase):
    """银行流水与资产快照对账报告审计表。"""

    __tablename__ = "reconciliation_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    as_of: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    matched: Mapped[bool] = mapped_column(Boolean, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)


class SQLiteDatabase:
    """统一创建并配置启用 WAL 模式的 SQLite 数据库引擎。"""

    _INIT_LOCK: ClassVar[Lock] = Lock()

    @staticmethod
    def _configure_connection(connection: sqlite3.Connection, _record: object) -> None:
        """建立连接时统一启用 WAL、外键约束、忙等待超时与安全删除。"""
        connection.execute("PRAGMA journal_mode = WAL").close()
        connection.execute("PRAGMA synchronous = NORMAL").close()
        connection.execute("PRAGMA busy_timeout = 5000").close()
        connection.execute("PRAGMA foreign_keys = ON").close()
        connection.execute("PRAGMA secure_delete = ON").close()

    @classmethod
    def create_engine_for_path(cls, path: str | Path, *, hide_parameters: bool = False) -> Engine:
        """创建受限权限的 SQLite 引擎并确保全部规范表已初始化。"""
        db_path = Path(path)
        with cls._INIT_LOCK:
            db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            db_path.chmod(0o600)
            engine = create_engine(
                URL.create("sqlite", database=str(db_path)),
                connect_args={"timeout": 5},
                poolclass=NullPool,
                hide_parameters=hide_parameters,
            )
            event.listen(engine, "connect", cls._configure_connection)
            QuantBase.metadata.create_all(engine)
            return engine
