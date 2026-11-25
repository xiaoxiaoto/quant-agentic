"""参考行情 SQLite 规范表与日志仓储，避免与银行双边报价混用。"""

import hashlib
import json
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from src.contracts.domains.reference_quote import ReferenceQuote
from src.repositories.database import ReferenceQuoteRecord, SQLiteDatabase


class ReferenceQuoteRepository:
    """将采集的参考行情写入 SQLite `reference_quotes` 表，并保留按日 JSONL 审计日志。"""

    def __init__(
        self,
        storage_dir: str | Path,
        *,
        database_path: str | Path | None = None,
        write_jsonl: bool = False,
    ) -> None:
        self._storage_dir = Path(storage_dir)
        self._write_jsonl = write_jsonl
        if self._write_jsonl:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
        if database_path is not None:
            self._db_path = Path(database_path)
        elif self._storage_dir.name == "references":
            self._db_path = self._storage_dir.parent / "quant-agentic.sqlite3"
        else:
            self._db_path = self._storage_dir / "quant-agentic.sqlite3"
        self._engine = SQLiteDatabase.create_engine_for_path(self._db_path)
        self._write_lock = Lock()
        self._imported_mtimes: dict[Path, float] = {}

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
        found: set[str] = set()
        with self._write_lock, Session(self._engine) as session, session.begin():
            for path, mtime in pending:
                if self._imported_mtimes.get(path) == mtime:
                    continue
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    quote = ReferenceQuote.model_validate_json(line)
                    if quote.record_hash != self._record_hash(quote):
                        raise ValueError("参考行情记录完整性校验失败")
                    self._insert_row(session, quote)
                    found.add(quote.product_code)
                self._imported_mtimes[path] = mtime
                if stop_when_products is not None and stop_when_products.issubset(found):
                    break

    def _query_latest_from_db(self, target_products: Collection[str] | None = None) -> dict[str, ReferenceQuote]:
        subq = select(
            ReferenceQuoteRecord.product_code,
            func.max(ReferenceQuoteRecord.id).label("max_id"),
        )
        if target_products is not None:
            targets = list(target_products)
            if not targets:
                return {}
            subq = subq.where(ReferenceQuoteRecord.product_code.in_(targets))
        subq = subq.group_by(ReferenceQuoteRecord.product_code).subquery()

        stmt = select(ReferenceQuoteRecord.raw_json).join(
            subq,
            ReferenceQuoteRecord.id == subq.c.max_id,
        )
        with Session(self._engine) as session:
            rows = session.execute(stmt).scalars().all()

        latest: dict[str, ReferenceQuote] = {}
        for raw_json in rows:
            quote = ReferenceQuote.model_validate_json(raw_json)
            if quote.record_hash != self._record_hash(quote):
                raise ValueError("参考行情记录完整性校验失败")
            latest[quote.product_code] = quote
        return latest

    def save(self, quote: ReferenceQuote) -> None:
        """写入 SQLite 参考行情表（默认不写本地 JSONL 文件）。"""
        quote.record_hash = self._record_hash(quote)
        with self._write_lock:
            with Session(self._engine) as session, session.begin():
                self._insert_row(session, quote)
            if self._write_jsonl:
                self._storage_dir.mkdir(parents=True, exist_ok=True)
                path = self._storage_dir / f"references_{quote.received_at:%Y-%m-%d}.jsonl"
                line = quote.model_dump_json() + "\n"
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(line)
                try:
                    self._imported_mtimes[path] = path.stat().st_mtime
                except OSError:
                    pass

    def latest(self, product_code: str = "CMBC-JCJ") -> ReferenceQuote | None:
        """读取指定品种最近完整记录，避免不同品种互相覆盖。"""
        return self.latest_all(target_products=(product_code,)).get(product_code)

    def latest_all(self, target_products: Collection[str] | None = None) -> dict[str, ReferenceQuote]:
        """从 SQLite 索引表读取各品种最新参考行情并校验完整性；若已找齐目标则不扫描历史日志。"""
        targets = set(target_products) if target_products is not None else None
        latest = self._query_latest_from_db(target_products)
        if targets is not None and targets.issubset(latest):
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
        limit: int = 500,
    ) -> list[ReferenceQuote]:
        """按时间区间查询已入库的单品种参考行情历史序列。"""
        self._sync_jsonl_files()
        stmt = select(ReferenceQuoteRecord.raw_json).where(ReferenceQuoteRecord.product_code == product_code)
        if start_time is not None:
            stmt = stmt.where(ReferenceQuoteRecord.received_at_utc >= self._format_utc(start_time))
        if end_time is not None:
            stmt = stmt.where(ReferenceQuoteRecord.received_at_utc <= self._format_utc(end_time))
        stmt = stmt.order_by(desc(ReferenceQuoteRecord.received_at_utc), desc(ReferenceQuoteRecord.id)).limit(limit)

        with Session(self._engine) as session:
            rows = session.execute(stmt).scalars().all()
        items = [ReferenceQuote.model_validate_json(raw) for raw in rows]
        items.reverse()
        return items
