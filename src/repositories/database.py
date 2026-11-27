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
- kline_bars / time_sharing_points：多周期 K 线与分时数据采集表
- jd_news_articles / jd_collection_checkpoints / jd_news_flash_scan / jd_collection_snapshots：京东资讯与持仓快照采集表
"""

import asyncio
import os
import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from threading import Lock
from typing import ClassVar

from sqlalchemy import URL, Boolean, Engine, Float, Index, Integer, Text, UniqueConstraint, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool

from src.contracts.exceptions import StorageError


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
        Index("ix_ref_quotes_product_received_id", "product_code", "received_at_utc", "id"),
        Index(
            "ix_ref_quotes_product_valid_received_id",
            "product_code",
            "received_at_utc",
            "id",
            sqlite_where=text("quote_at_utc IS NOT NULL AND quality_state = 'NORMAL'"),
        ),
        Index("ix_ref_quotes_product_hash", "product_code", "record_hash"),
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


class ReferenceQuoteImportRecord(QuantBase):
    """持久化旧行情日志已经导入的文件版本。"""

    __tablename__ = "reference_quote_imports"

    filename: Mapped[str] = mapped_column(Text, primary_key=True)
    mtime_ns: Mapped[int] = mapped_column(Integer)


class KLineBarRecord(QuantBase):
    """多周期 K 线行情采集表。"""

    __tablename__ = "market_kline_bars"
    __table_args__ = (
        UniqueConstraint("product_code", "interval", "source", "bar_time_utc", name="uq_market_kline_bar"),
        Index("ix_market_kline_product_interval_time", "product_code", "interval", "bar_time_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_code: Mapped[str] = mapped_column(Text, nullable=False)
    interval: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    bar_time_utc: Mapped[str] = mapped_column(Text, nullable=False)
    trade_date: Mapped[str] = mapped_column(Text, nullable=False)
    open_price: Mapped[float] = mapped_column(Float, nullable=False)
    high_price: Mapped[float] = mapped_column(Float, nullable=False)
    low_price: Mapped[float] = mapped_column(Float, nullable=False)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    pre_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    collected_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
    available_at_utc: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)


class TimeSharingRecord(QuantBase):
    """分时走势点位采集表。"""

    __tablename__ = "market_time_sharing"
    __table_args__ = (
        UniqueConstraint("product_code", "granularity", "source", "trade_time_utc", name="uq_market_time_sharing"),
        Index("ix_market_timesharing_product_gran_time", "product_code", "granularity", "trade_time_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_code: Mapped[str] = mapped_column(Text, nullable=False)
    granularity: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    trade_time_utc: Mapped[str] = mapped_column(Text, nullable=False)
    show_time: Mapped[str] = mapped_column(Text, nullable=False)
    last_price: Mapped[float] = mapped_column(Float, nullable=False)
    pre_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    collected_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)


class LedgerOrderRecord(QuantBase):
    """影子账本成交订单明细表。"""

    __tablename__ = "ledger_orders"
    __table_args__ = (Index("ix_ledger_orders_scope_seq", "scope", "seq"),)

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


class JDNewsArticleRecord(QuantBase):
    """京东资讯、快讯与早报原文采集表。"""

    __tablename__ = "jd_news_articles"
    __table_args__ = (
        Index("ix_jd_news_channel_published", "channel", "published_at"),
        Index("ix_jd_news_processed_attempt_pub", "processed", "attempted_at", "published_at"),
    )

    article_id: Mapped[str] = mapped_column(Text, primary_key=True)
    channel: Mapped[str] = mapped_column(Text)
    published_at: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    raw_json: Mapped[str] = mapped_column(Text)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attempted_at: Mapped[str | None] = mapped_column(Text)


class JDCollectionCheckpointRecord(QuantBase):
    """京东资讯频道扫描进度检查点表。"""

    __tablename__ = "jd_collection_checkpoints"

    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    article_id: Mapped[str] = mapped_column(Text)


class JDFlashScanRecord(QuantBase):
    """京东快讯分页扫描断点游标表。"""

    __tablename__ = "jd_news_flash_scan"

    id: Mapped[int] = mapped_column(primary_key=True)
    head: Mapped[str] = mapped_column(Text)
    cursor: Mapped[str] = mapped_column(Text)


class JDSnapshotRecord(QuantBase):
    """京东持仓与汇率增量快照表。"""

    __tablename__ = "jd_collection_snapshots"
    __table_args__ = (Index("ix_jd_snapshots_scope_id", "scope", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(Text)
    raw_json: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[str] = mapped_column(Text)
    checked_at: Mapped[str] = mapped_column(Text)


class SQLiteDatabase:
    """统一创建并配置启用 WAL 模式的 SQLite 数据库引擎。"""

    _INIT_LOCK: ClassVar[Lock] = Lock()

    @staticmethod
    def _configure_connection(connection: sqlite3.Connection, _record: object) -> None:
        """建立连接时统一启用同步模式、忙等待超时、外键约束与安全删除。"""
        connection.execute("PRAGMA synchronous = NORMAL").close()
        connection.execute("PRAGMA busy_timeout = 5000").close()
        connection.execute("PRAGMA foreign_keys = ON").close()
        connection.execute("PRAGMA secure_delete = ON").close()

    @staticmethod
    def _migrate_schema(engine: Engine) -> None:
        """为已有 SQLite 库幂等补齐新增标量列与部分索引。"""
        with engine.connect() as conn:
            cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(market_kline_bars)").fetchall()}
            idx_exists = (
                conn.exec_driver_sql(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'index' AND name = 'ix_ref_quotes_product_valid_received_id'"
                ).fetchone()
                is not None
            )
        if {"available_at_utc", "origin_source"}.issubset(cols) and idx_exists:
            return
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(market_kline_bars)").fetchall()}
                if "available_at_utc" not in cols:
                    conn.exec_driver_sql("ALTER TABLE market_kline_bars ADD COLUMN available_at_utc TEXT")
                if "origin_source" not in cols:
                    conn.exec_driver_sql("ALTER TABLE market_kline_bars ADD COLUMN origin_source TEXT")
                conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_ref_quotes_product_valid_received_id "
                    "ON reference_quotes (product_code, received_at_utc, id) "
                    "WHERE quote_at_utc IS NOT NULL AND quality_state = 'NORMAL'"
                )
                conn.exec_driver_sql("COMMIT")
            except BaseException:
                with suppress(Exception):
                    conn.exec_driver_sql("ROLLBACK")
                raise

    @classmethod
    def prepare_sqlite_file(cls, path: str | Path) -> Path:
        """创建受限权限（0o700 目录 / 0o600 文件）的 SQLite 数据库文件及 WAL 侧文件。"""
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with suppress(OSError):
            db_path.parent.chmod(0o700)
        fd = os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        db_path.chmod(0o600)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{db_path}{suffix}")
            if sidecar.exists():
                with suppress(OSError):
                    sidecar.chmod(0o600)
        return db_path

    @classmethod
    def create_wal_engine(cls, path: str | Path, *, hide_parameters: bool = False) -> Engine:
        """创建启用 WAL 与受限权限的裸 SQLite 引擎（不自动建全部业务表）。"""
        db_path = cls.prepare_sqlite_file(path)
        engine = create_engine(
            URL.create("sqlite", database=str(db_path)),
            connect_args={"timeout": 5},
            poolclass=NullPool,
            hide_parameters=hide_parameters,
        )
        event.listen(engine, "connect", cls._configure_connection)
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode = WAL")
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{db_path}{suffix}")
            if sidecar.exists():
                with suppress(OSError):
                    sidecar.chmod(0o600)
        return engine

    @classmethod
    @contextmanager
    def write_session(cls, engine: Engine) -> Generator[Session]:
        """以 BEGIN IMMEDIATE 开启 SQLite 写事务，避免 WAL 模式下 DEFERRED 升级绕过 busy_timeout。"""
        try:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                session = Session(bind=conn, autoflush=False)
                try:
                    yield session
                    session.flush()
                    conn.exec_driver_sql("COMMIT")
                except BaseException:
                    with suppress(Exception):
                        conn.exec_driver_sql("ROLLBACK")
                    raise
                finally:
                    session.close()
        except SQLAlchemyError as exc:
            raise StorageError("SQLite 写事务执行失败") from exc

    @staticmethod
    async def run_shielded_write[T, **P](operation: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """在协程被取消时等待在途 SQLite 写线程完成收尾，避免提前脱离或重复取消中断。"""
        task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            if not task.cancelled():
                task.exception()
            raise asyncio.CancelledError
        return task.result()

    @classmethod
    def schema_lock(cls) -> Lock:
        """返回用于串行化 SQLite 建表与结构迁移的进程内互斥锁。"""
        return cls._INIT_LOCK

    @classmethod
    def create_engine_for_path(cls, path: str | Path, *, hide_parameters: bool = False) -> Engine:
        """创建受限权限的 SQLite 引擎并确保全部规范表及增量结构已初始化。"""
        with cls._INIT_LOCK:
            engine = cls.create_wal_engine(path, hide_parameters=hide_parameters)
            QuantBase.metadata.create_all(engine)
            cls._migrate_schema(engine)
            return engine
