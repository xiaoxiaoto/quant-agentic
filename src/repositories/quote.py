"""时间点（Point-in-Time, PIT）行情仓储模块。

采用 SQLite（WAL 模式）规范表作为主存储与索引查询引擎，并保留按日 JSONL 审计日志。
严格遵循 available_at <= decision_at 的 PIT 原则，避免前视偏差。
"""

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from sqlalchemy import desc, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState
from src.repositories.database import QuantBase, QuoteRecord, SQLiteDatabase


class QuoteRepository:
    """时间点（Point-in-Time）双边行情数据仓储。

    将行情写入 SQLite `quotes` 规范表并提供索引化 PIT 检索，同时保留按日 JSONL 审计日志。
    """

    def __init__(
        self,
        storage_dir: Path | str,
        *,
        database_path: Path | str | None = None,
        write_jsonl: bool = False,
    ) -> None:
        self._storage_dir = Path(storage_dir)
        self._write_jsonl = write_jsonl
        if self._write_jsonl:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = (
            Path(database_path) if database_path is not None else self._storage_dir / "quant-agentic.sqlite3"
        )
        self._engine = SQLiteDatabase.create_engine_for_path(self._db_path)
        # 已存在的行情表不会由 create_all 补建新索引，此处按需幂等补齐
        for index in QuantBase.metadata.tables[QuoteRecord.__tablename__].indexes:
            index.create(self._engine, checkfirst=True)
        self._write_lock = Lock()
        self._imported_mtimes: dict[Path, float] = {}

    @staticmethod
    def _to_utc(dt: datetime) -> datetime:
        """统一将时间标准化为带时区的 UTC，兼容历史无时区日志记录。"""
        return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    @classmethod
    def _format_utc(cls, dt: datetime) -> str:
        """格式化为定长 UTC 字符串，确保 SQLite 文本索引按时间精确排序与范围比较。"""
        return cls._to_utc(dt).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @classmethod
    def _pit_cutoff(cls, quote: QuoteRaw) -> datetime:
        """计算单条行情满足 PIT 可见的生效截断时间（UTC）。"""
        received_utc = cls._to_utc(quote.received_at)
        available_utc = cls._to_utc(quote.available_at) if quote.available_at is not None else received_utc
        return max(cls._to_utc(quote.quote_at), received_utc, available_utc)

    @classmethod
    def _normalize_quote_tz(cls, quote: QuoteRaw) -> QuoteRaw:
        """将查询返回记录中的无时区时间戳统一补全为 UTC。"""
        updates: dict[str, datetime] = {}
        if quote.quote_at.tzinfo is None:
            updates["quote_at"] = quote.quote_at.replace(tzinfo=UTC)
        if quote.received_at.tzinfo is None:
            updates["received_at"] = quote.received_at.replace(tzinfo=UTC)
        if quote.available_at is not None and quote.available_at.tzinfo is None:
            updates["available_at"] = quote.available_at.replace(tzinfo=UTC)
        return quote.model_copy(update=updates) if updates else quote

    def _parse_line(self, line: str, *, normalize_tz: bool = True) -> QuoteRaw | None:
        """解析单行 JSONL 行情记录；查询路径默认将无时区时间补全为 UTC。"""
        clean_line = line.strip()
        if not clean_line:
            return None
        try:
            data = json.loads(clean_line)
            quote = QuoteRaw.model_validate(data)
        except Exception:
            return None
        return self._normalize_quote_tz(quote) if normalize_tz else quote

    def _insert_row(self, session: Session, quote: QuoteRaw) -> None:
        """以幂等方式将单条双边行情写入 SQLite 规范表。"""
        pit_cutoff = self._pit_cutoff(quote)
        stmt = (
            insert(QuoteRecord)
            .values(
                quote_id=quote.quote_id,
                source_id=quote.source_id,
                product_code=quote.product_code,
                bid_price=quote.bid_price,
                ask_price=quote.ask_price,
                quote_at_utc=self._format_utc(quote.quote_at),
                received_at_utc=self._format_utc(quote.received_at),
                available_at_utc=self._format_utc(quote.available_at) if quote.available_at is not None else None,
                pit_cutoff_utc=self._format_utc(pit_cutoff),
                is_tradable=quote.is_tradable,
                is_synthetic=quote.is_synthetic,
                response_code=quote.response_code,
                payload_hash=quote.payload_hash,
                record_hash=quote.record_hash,
                quality_state=quote.quality_state.value,
                raw_json=json.dumps(quote.model_dump(mode="json"), ensure_ascii=False),
            )
            .on_conflict_do_nothing(index_elements=[QuoteRecord.quote_id])
        )
        session.execute(stmt)

    def _sync_jsonl_files(self) -> None:
        """将存储目录中尚未同步的历史或外部写入 JSONL 行情幂等导入 SQLite。"""
        if not self._storage_dir.exists():
            return
        files = sorted(self._storage_dir.glob("quotes_*.jsonl"))
        pending: list[tuple[Path, float]] = []
        for path in files:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if self._imported_mtimes.get(path) != mtime:
                pending.append((path, mtime))
        if not pending:
            return
        with self._write_lock, Session(self._engine) as session, session.begin():
            for path, mtime in pending:
                if self._imported_mtimes.get(path) == mtime:
                    continue
                try:
                    with open(path, encoding="utf-8") as stream:
                        for line in stream:
                            quote = self._parse_line(line, normalize_tz=False)
                            if quote is not None:
                                self._insert_row(session, quote)
                    self._imported_mtimes[path] = mtime
                except OSError:
                    continue

    @staticmethod
    def compute_record_hash(quote: QuoteRaw) -> str:
        """计算不含校验字段的完整记录哈希；默认非合成标记不参与哈希以兼容历史日志。"""
        excluded = {"record_hash"}
        if not quote.is_synthetic:
            excluded.add("is_synthetic")
        record = quote.model_dump(mode="json", exclude=excluded)
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def save_quote(self, quote: QuoteRaw) -> None:
        """将单条行情写入 SQLite 规范表（默认不写本地 JSONL 文件）。"""
        quote.record_hash = self.compute_record_hash(quote)
        with self._write_lock:
            with Session(self._engine) as session, session.begin():
                self._insert_row(session, quote)
            if self._write_jsonl:
                self._storage_dir.mkdir(parents=True, exist_ok=True)
                date_str = quote.received_at.strftime("%Y-%m-%d")
                file_path = self._storage_dir / f"quotes_{date_str}.jsonl"
                line = json.dumps(quote.model_dump(mode="json"), ensure_ascii=False) + "\n"
                with open(file_path, "a", encoding="utf-8") as stream:
                    stream.write(line)
                try:
                    self._imported_mtimes[file_path] = file_path.stat().st_mtime
                except OSError:
                    pass

    def get_latest_as_of(
        self,
        as_of: datetime,
        product_code: str = "GOLD_ACCUMULATION",
        *,
        exclude_synthetic: bool = False,
    ) -> QuoteRaw | None:
        """获取在指定历史时间点已可见的最优最新行情。

        仅返回 available_at <= as_of 的有效记录，杜绝未来信息穿越。
        """
        self._sync_jsonl_files()
        as_of_str = self._format_utc(as_of)
        stmt = (
            select(QuoteRecord.raw_json)
            .where(
                QuoteRecord.product_code == product_code,
                QuoteRecord.quality_state == DataQualityState.NORMAL.value,
                QuoteRecord.quote_at_utc <= as_of_str,
                QuoteRecord.pit_cutoff_utc <= as_of_str,
            )
            .order_by(desc(QuoteRecord.quote_at_utc), desc(QuoteRecord.id))
            .limit(1)
        )
        if exclude_synthetic:
            stmt = stmt.where(QuoteRecord.is_synthetic.is_(False))
        with Session(self._engine) as session:
            raw_json = session.execute(stmt).scalar_one_or_none()
        if raw_json is None:
            return None
        return self._normalize_quote_tz(QuoteRaw.model_validate_json(raw_json))

    def query_pit_series(
        self,
        start_time: datetime,
        end_time: datetime,
        as_of: datetime | None = None,
        product_code: str = "GOLD_ACCUMULATION",
        valid_only: bool = True,
        *,
        exclude_synthetic: bool = False,
    ) -> list[QuoteRaw]:
        """检索时间范围内满足 PIT 约束的行情序列。"""
        self._sync_jsonl_files()
        start_str = self._format_utc(start_time)
        end_str = self._format_utc(end_time)
        effective_as_of_str = self._format_utc(as_of or end_time)

        stmt = (
            select(QuoteRecord.raw_json)
            .where(
                QuoteRecord.product_code == product_code,
                QuoteRecord.quote_at_utc >= start_str,
                QuoteRecord.quote_at_utc <= end_str,
                QuoteRecord.pit_cutoff_utc <= effective_as_of_str,
            )
            .order_by(QuoteRecord.quote_at_utc, QuoteRecord.id)
        )
        if valid_only:
            stmt = stmt.where(QuoteRecord.quality_state == DataQualityState.NORMAL.value)
        if exclude_synthetic:
            stmt = stmt.where(QuoteRecord.is_synthetic.is_(False))

        with Session(self._engine) as session:
            rows = session.execute(stmt).scalars().all()
        return [self._normalize_quote_tz(QuoteRaw.model_validate_json(raw)) for raw in rows]

    def iter_replay(
        self,
        start_time: datetime,
        end_time: datetime,
        product_code: str = "GOLD_ACCUMULATION",
        *,
        exclude_synthetic: bool = False,
    ) -> Iterator[QuoteRaw]:
        """按时间顺序回放行情流，用于无前视偏差的仿真回测。"""
        series = self.query_pit_series(
            start_time=start_time,
            end_time=end_time,
            as_of=end_time,
            product_code=product_code,
            exclude_synthetic=exclude_synthetic,
        )
        series.sort(key=self._pit_cutoff)
        yield from series

    def verify_file_integrity(self, file_path: Path) -> tuple[int, int]:
        """核验行情日志文件的哈希完整性。

        返回 (有效校验数, 异常破坏数)。
        """
        valid_count = 0
        corrupt_count = 0
        if not file_path.exists():
            return 0, 0

        with open(file_path, encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                quote = self._parse_line(line, normalize_tz=False)
                if quote is None:
                    corrupt_count += 1
                    continue
                if quote.record_hash:
                    if quote.record_hash == self.compute_record_hash(quote):
                        valid_count += 1
                    else:
                        corrupt_count += 1
                elif quote.payload_hash:
                    expected = hashlib.sha256(
                        f"{quote.quote_id}:{quote.bid_price}:{quote.ask_price}".encode()
                    ).hexdigest()
                    if quote.payload_hash == expected:
                        valid_count += 1
                    else:
                        corrupt_count += 1
                else:
                    valid_count += 1

        return valid_count, corrupt_count
