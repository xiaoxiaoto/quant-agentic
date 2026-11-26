"""京东资讯、持仓及换算汇率的增量存储。"""

import json
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import JsonValue
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from src.contracts.domains.jd_collection import JDNewsArticle
from src.repositories.database import (
    JDCollectionCheckpointRecord,
    JDFlashScanRecord,
    JDNewsArticleRecord,
    JDSnapshotRecord,
    SQLiteDatabase,
)


class JDCollectionRepository:
    """以 SQLite 为准保存增量原文、分页进度和变化后的快照。"""

    def __init__(self, database_path: Path) -> None:
        self._engine = SQLiteDatabase.create_engine_for_path(database_path, hide_parameters=True)

    @staticmethod
    def _latest_snapshot(session: Session, scope: str) -> JDSnapshotRecord | None:
        return session.scalar(
            select(JDSnapshotRecord)
            .where(JDSnapshotRecord.scope == scope)
            .order_by(JDSnapshotRecord.id.desc())
            .limit(1)
        )

    @staticmethod
    def _format_utc(dt: datetime) -> str:
        utc_dt = dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def save_snapshot(self, scope: str, data: JsonValue, checked_at: datetime) -> None:
        """相同内容仅更新确认时间，内容变化则追加一份快照。"""
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        checked = self._format_utc(checked_at)
        with SQLiteDatabase.write_session(self._engine) as session:
            latest = self._latest_snapshot(session, scope)
            if latest is not None and latest.checked_at >= checked:
                return
            if latest is not None and latest.raw_json == raw:
                latest.checked_at = checked
            else:
                session.add(JDSnapshotRecord(scope=scope, raw_json=raw, collected_at=checked, checked_at=checked))

    def latest_snapshot(self, scope: str) -> tuple[JsonValue, datetime, datetime] | None:
        """读取最近成功的快照、内容时间和确认时间。"""
        with Session(self._engine) as session:
            row = self._latest_snapshot(session, scope)
            if row is None:
                return None
            return (
                cast(JsonValue, json.loads(row.raw_json)),
                datetime.fromisoformat(row.collected_at),
                datetime.fromisoformat(row.checked_at),
            )

    def checkpoint(self, channel: str) -> str | None:
        """返回上次完成扫描时最新的资讯标识。"""
        with Session(self._engine) as session:
            row = session.get(JDCollectionCheckpointRecord, channel)
            return row.article_id if row else None

    def flash_scan(self) -> tuple[str, str] | None:
        """恢复未完成的快讯扫描起点和已提交页的游标。"""
        with Session(self._engine) as session:
            scan = session.get(JDFlashScanRecord, 1)
            return (scan.head, scan.cursor) if scan is not None else None

    def save_articles(
        self,
        articles: list[JDNewsArticle],
        *,
        checkpoint: str | None = None,
        checkpoint_channel: str = "flash",
        flash_scan: tuple[str, str] | None = None,
    ) -> None:
        """事务内写入新文章及扫描进度。"""
        with SQLiteDatabase.write_session(self._engine) as session:
            for article in articles:
                session.execute(
                    insert(JDNewsArticleRecord)
                    .values(
                        article_id=article.article_id,
                        channel=article.channel,
                        published_at=self._format_utc(article.published_at),
                        title=article.title,
                        content=article.content,
                        raw_json=article.model_dump_json(),
                        processed=False,
                    )
                    .on_conflict_do_nothing(index_elements=[JDNewsArticleRecord.article_id])
                )
            if flash_scan is not None:
                head, cursor = flash_scan
                session.merge(JDFlashScanRecord(id=1, head=head, cursor=cursor))
            if checkpoint is not None:
                session.merge(JDCollectionCheckpointRecord(channel=checkpoint_channel, article_id=checkpoint))
                if checkpoint_channel == "flash":
                    scan = session.get(JDFlashScanRecord, 1)
                    if scan is not None:
                        session.delete(scan)

    def list_articles(self, *, limit: int, channel: str | None = None, query: str | None = None) -> list[JDNewsArticle]:
        """按发布时间读取已落库资讯。"""
        stmt = select(JDNewsArticleRecord.raw_json)
        if channel is not None:
            stmt = stmt.where(JDNewsArticleRecord.channel == channel)
        if query:
            stmt = stmt.where(
                or_(
                    JDNewsArticleRecord.title.contains(query, autoescape=True),
                    JDNewsArticleRecord.content.contains(query, autoescape=True),
                )
            )
        stmt = stmt.order_by(JDNewsArticleRecord.published_at.desc(), JDNewsArticleRecord.article_id).limit(limit)
        with Session(self._engine) as session:
            return [JDNewsArticle.model_validate_json(raw) for raw in session.scalars(stmt)]

    def pending_articles(self, limit: int, *, exclude: set[str] | None = None) -> list[JDNewsArticle]:
        """读取待提取风险的持久化原文，包括此前失败的记录。"""
        stmt = select(JDNewsArticleRecord.raw_json).where(JDNewsArticleRecord.processed.is_(False))
        use_sql_exclude = bool(exclude) and len(exclude) <= 500
        if exclude and use_sql_exclude:
            stmt = stmt.where(JDNewsArticleRecord.article_id.not_in(exclude))
        fetch_limit = limit if use_sql_exclude or not exclude else limit + len(exclude)
        with Session(self._engine) as session:
            rows = [
                JDNewsArticle.model_validate_json(raw)
                for raw in session.scalars(
                    stmt.order_by(
                        JDNewsArticleRecord.attempted_at.asc(), JDNewsArticleRecord.published_at.desc()
                    ).limit(fetch_limit)
                )
            ]
        if exclude and not use_sql_exclude:
            rows = [item for item in rows if item.article_id not in exclude]
        return rows[:limit]

    def filter_unprocessed_ids(self, article_ids: Collection[str]) -> set[str]:
        """批量分块查询给定文章标识中仍处于未处理状态的集合。"""
        ids = list(dict.fromkeys(article_ids))
        if not ids:
            return set()
        result: set[str] = set()
        with Session(self._engine) as session:
            for idx in range(0, len(ids), 500):
                chunk = ids[idx : idx + 500]
                stmt = select(JDNewsArticleRecord.article_id).where(
                    JDNewsArticleRecord.article_id.in_(chunk),
                    JDNewsArticleRecord.processed.is_(False),
                )
                result.update(session.scalars(stmt).all())
        return result

    def mark_processed(self, article_id: str) -> None:
        """将已提取或已存在等价风险事件的资讯移出待处理列表。"""
        self.mark_processed_batch((article_id,))

    def mark_processed_batch(self, article_ids: Collection[str]) -> None:
        """在单次写事务内分块批量将文章标记为已处理。"""
        ids = list(dict.fromkeys(article_ids))
        if not ids:
            return
        with SQLiteDatabase.write_session(self._engine) as session:
            for idx in range(0, len(ids), 500):
                chunk = ids[idx : idx + 500]
                session.execute(
                    update(JDNewsArticleRecord).where(JDNewsArticleRecord.article_id.in_(chunk)).values(processed=True)
                )

    def mark_attempt(self, article_id: str, attempted_at: datetime) -> None:
        """记录失败尝试时间，下一轮优先处理尚未尝试的积压。"""
        self.mark_attempts_batch((article_id,), attempted_at)

    def mark_attempts_batch(self, article_ids: Collection[str], attempted_at: datetime) -> None:
        """在单次写事务内分块批量记录失败尝试时间。"""
        ids = list(dict.fromkeys(article_ids))
        if not ids:
            return
        formatted = self._format_utc(attempted_at)
        with SQLiteDatabase.write_session(self._engine) as session:
            for idx in range(0, len(ids), 500):
                chunk = ids[idx : idx + 500]
                session.execute(
                    update(JDNewsArticleRecord)
                    .where(JDNewsArticleRecord.article_id.in_(chunk))
                    .values(attempted_at=formatted)
                )
