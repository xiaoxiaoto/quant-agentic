"""参考行情 SQLite 规范表与日志仓储，避免与银行双边报价混用。"""

import hashlib
import json
import logging
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from pydantic import ValidationError
from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

from src.contracts.domains.reference_quote import ReferenceQuote
from src.repositories.database import ReferenceQuoteImportRecord, ReferenceQuoteRecord, SQLiteDatabase


class ReferenceQuoteRepository:
    """将采集的参考行情写入 SQLite `reference_quotes` 表，并保留按日 JSONL 审计日志。"""

    def __init__(
        self,
        storage_dir: str | Path,
        *,
        database_path: str | Path | None = None,
        write_jsonl: bool = False,
        import_jsonl_on_read: bool = True,
    ) -> None:
        self._storage_dir = Path(storage_dir)
        self._write_jsonl = write_jsonl
        self._import_jsonl_on_read = import_jsonl_on_read
        if self._write_jsonl:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
        if database_path is not None:
            self._db_path = Path(database_path)
        elif self._storage_dir.name == "references":
            self._db_path = self._storage_dir.parent / "quant-agentic.sqlite3"
        else:
            self._db_path = self._storage_dir / "quant-agentic.sqlite3"
        self._engine = SQLiteDatabase.create_engine_for_path(self._db_path)
        with SQLiteDatabase.schema_lock(), SQLiteDatabase.write_session(self._engine) as session:
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_ref_quotes_product_hash "
                    "ON reference_quotes (product_code, record_hash)"
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_ref_quotes_product_received_id "
                    "ON reference_quotes (product_code, received_at_utc, id)"
                )
            )
        self._write_lock = Lock()
        self._imported_mtimes: dict[Path, int] = {}

    @staticmethod
    def _format_utc(dt: datetime) -> str:
        utc_dt = dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @staticmethod
    def _record_hash(quote: ReferenceQuote) -> str:
        excluded = {"record_hash"}
        # 默认方向不参与哈希，兼容引入多品种之前的民生历史记录。
        if quote.price_side == "reference":
            excluded.add("price_side")
        record = quote.model_dump(mode="json", exclude=excluded)
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def _insert_row(self, session: Session, quote: ReferenceQuote) -> None:
        """写入单条参考行情记录，按产品编码与记录哈希去重。"""
        if quote.record_hash is not None:
            existing_id = session.execute(
                select(ReferenceQuoteRecord.id)
                .where(
                    ReferenceQuoteRecord.product_code == quote.product_code,
                    ReferenceQuoteRecord.record_hash == quote.record_hash,
                )
                .limit(1)
            ).scalar_one_or_none()
            if existing_id is not None:
                return
        session.add(
            ReferenceQuoteRecord(
                quote_id=quote.quote_id,
                source_id=quote.source_id,
                product_code=quote.product_code,
                unit=quote.unit,
                price_side=quote.price_side,
                price=quote.price,
                quote_at_utc=self._format_utc(quote.quote_at) if quote.quote_at is not None else None,
                received_at_utc=self._format_utc(quote.received_at),
                available_at_utc=self._format_utc(quote.available_at) if quote.available_at is not None else None,
                is_tradable=quote.is_tradable,
                quality_state=quote.quality_state.value,
                payload_hash=quote.payload_hash,
                record_hash=quote.record_hash,
                raw_json=quote.model_dump_json(),
            )
        )

    def _sync_jsonl_files(self, *, stop_when_products: set[str] | None = None) -> None:
        """倒序将尚未同步的历史或外部写入 references_*.jsonl 导入 SQLite；找齐目标品种后提前终止。"""
        if not self._storage_dir.exists():
            return
        files = sorted(self._storage_dir.glob("references_*.jsonl"), reverse=True)
        pending: list[tuple[Path, int]] = []
        for path in files:
            try:
                mtime = path.stat().st_mtime_ns
            except OSError:
                continue
            if self._imported_mtimes.get(path) != mtime:
                pending.append((path, mtime))
        if not pending:
            return
        found: set[str] = set()
        with self._write_lock:
            for path, mtime in pending:
                if self._imported_mtimes.get(path) == mtime:
                    continue
                filename = str(path.resolve())
                with Session(self._engine) as read_session:
                    version = read_session.get(ReferenceQuoteImportRecord, filename)
                    if version is not None and version.mtime_ns == mtime:
                        self._imported_mtimes[path] = mtime
                        continue

                # 在写事务外完成磁盘读取、Pydantic 解析与 SHA-256 完整性校验，避免长事务占用写锁。
                parsed_quotes: list[ReferenceQuote] = []
                for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    if not line.strip():
                        continue
                    try:
                        quote = ReferenceQuote.model_validate_json(line)
                    except ValidationError as exc:
                        logging.getLogger(__name__).warning(
                            "跳过损坏的参考行情 JSONL 行（%s:%d）：%s", path.name, line_no, exc
                        )
                        continue
                    if quote.record_hash != self._record_hash(quote):
                        raise ValueError("参考行情记录完整性校验失败")
                    parsed_quotes.append(quote)

                with SQLiteDatabase.write_session(self._engine) as session:
                    existing_pairs: set[tuple[str, str]] = set()
                    by_product: dict[str, set[str]] = {}
                    for quote in parsed_quotes:
                        if quote.record_hash is not None:
                            by_product.setdefault(quote.product_code, set()).add(quote.record_hash)
                    for product_code, hashes in by_product.items():
                        hash_list = list(hashes)
                        for idx in range(0, len(hash_list), 500):
                            chunk = hash_list[idx : idx + 500]
                            rows = session.execute(
                                select(ReferenceQuoteRecord.record_hash).where(
                                    ReferenceQuoteRecord.product_code == product_code,
                                    ReferenceQuoteRecord.record_hash.in_(chunk),
                                )
                            ).scalars()
                            for existing_hash in rows:
                                if existing_hash is not None:
                                    existing_pairs.add((product_code, existing_hash))

                    for quote in parsed_quotes:
                        found.add(quote.product_code)
                        if quote.record_hash is not None:
                            pair = (quote.product_code, quote.record_hash)
                            if pair in existing_pairs:
                                continue
                            existing_pairs.add(pair)
                        session.add(
                            ReferenceQuoteRecord(
                                quote_id=quote.quote_id,
                                source_id=quote.source_id,
                                product_code=quote.product_code,
                                unit=quote.unit,
                                price_side=quote.price_side,
                                price=quote.price,
                                quote_at_utc=self._format_utc(quote.quote_at) if quote.quote_at is not None else None,
                                received_at_utc=self._format_utc(quote.received_at),
                                available_at_utc=(
                                    self._format_utc(quote.available_at) if quote.available_at is not None else None
                                ),
                                is_tradable=quote.is_tradable,
                                quality_state=quote.quality_state.value,
                                payload_hash=quote.payload_hash,
                                record_hash=quote.record_hash,
                                raw_json=quote.model_dump_json(),
                            )
                        )
                    version_row = session.get(ReferenceQuoteImportRecord, filename)
                    if version_row is None:
                        session.add(ReferenceQuoteImportRecord(filename=filename, mtime_ns=mtime))
                    else:
                        version_row.mtime_ns = mtime
                self._imported_mtimes[path] = mtime
                if stop_when_products is not None and stop_when_products.issubset(found):
                    break

    def _query_latest_from_db(self, target_products: Collection[str] | None = None) -> dict[str, ReferenceQuote]:
        latest: dict[str, ReferenceQuote] = {}
        with Session(self._engine) as session:
            if target_products is not None:
                product_list = list(dict.fromkeys(target_products))
            else:
                product_list = list(session.scalars(select(ReferenceQuoteRecord.product_code).distinct()).all())
            if not product_list:
                return latest
            for product in product_list:
                raw = session.execute(
                    select(ReferenceQuoteRecord.raw_json)
                    .where(ReferenceQuoteRecord.product_code == product)
                    .order_by(desc(ReferenceQuoteRecord.received_at_utc), desc(ReferenceQuoteRecord.id))
                    .limit(1)
                ).scalar_one_or_none()
                if raw is None:
                    continue
                quote = ReferenceQuote.model_validate_json(raw)
                if quote.record_hash != self._record_hash(quote):
                    raise ValueError("参考行情记录完整性校验失败")
                latest[product] = quote
        return latest

    def save(self, quote: ReferenceQuote) -> None:
        """写入 SQLite 参考行情表（默认不写本地 JSONL 文件）。"""
        quote.record_hash = self._record_hash(quote)
        with self._write_lock:
            with SQLiteDatabase.write_session(self._engine) as session:
                self._insert_row(session, quote)
            if self._write_jsonl:
                self._storage_dir.mkdir(parents=True, exist_ok=True)
                path = self._storage_dir / f"references_{quote.received_at:%Y-%m-%d}.jsonl"
                line = quote.model_dump_json() + "\n"
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(line)
                try:
                    self._imported_mtimes[path] = path.stat().st_mtime_ns
                except OSError:
                    pass

    def import_history(self) -> None:
        """在服务启动时导入旧 JSONL 日志。"""
        self._sync_jsonl_files()

    def latest(self, product_code: str = "CMBC-JCJ") -> ReferenceQuote | None:
        """读取指定品种最近完整记录，避免不同品种互相覆盖。"""
        return self.latest_all(target_products=(product_code,)).get(product_code)

    def latest_all(self, target_products: Collection[str] | None = None) -> dict[str, ReferenceQuote]:
        """从 SQLite 索引表读取各品种最新参考行情并校验完整性；若已找齐目标则不扫描历史日志。"""
        targets = set(target_products) if target_products is not None else None
        latest = self._query_latest_from_db(target_products)
        if not self._import_jsonl_on_read or (targets is not None and targets.issubset(latest)):
            return latest
        missing = (targets - set(latest)) if targets is not None else None
        self._sync_jsonl_files(stop_when_products=missing)
        return self._query_latest_from_db(target_products)

    def query_series(
        self,
        product_code: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int | None = 500,
    ) -> list[ReferenceQuote]:
        """按时间区间查询已入库的单品种参考行情历史序列；传入 limit=None 返回区间全量序列。"""
        if self._import_jsonl_on_read:
            self._sync_jsonl_files()
        stmt = select(ReferenceQuoteRecord.raw_json).where(ReferenceQuoteRecord.product_code == product_code)
        if start_time is not None:
            stmt = stmt.where(ReferenceQuoteRecord.received_at_utc >= self._format_utc(start_time))
        if end_time is not None:
            stmt = stmt.where(ReferenceQuoteRecord.received_at_utc <= self._format_utc(end_time))
        stmt = stmt.order_by(desc(ReferenceQuoteRecord.received_at_utc), desc(ReferenceQuoteRecord.id))
        if limit is not None:
            stmt = stmt.limit(limit)

        with Session(self._engine) as session:
            rows = session.execute(stmt).scalars().all()
        items = [ReferenceQuote.model_validate_json(raw) for raw in rows]
        items.reverse()
        return items
