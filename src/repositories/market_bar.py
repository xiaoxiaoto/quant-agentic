"""多周期 K 线与分时行情的 SQLite 批量去重存储与查询仓储。"""

from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import ClassVar

from sqlalchemy import desc, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from src.contracts.domains.market_bar import KLineBar, TimeSharingPoint
from src.repositories.database import KLineBarRecord, SQLiteDatabase, TimeSharingRecord


class MarketBarRepository:
    """将多周期 K 线与分时点位批量幂等写入 SQLite 并提供时间序列查询。"""

    _BATCH_SIZE: ClassVar[int] = 50

    def __init__(self, database_path: str | Path) -> None:
        self._engine = SQLiteDatabase.create_engine_for_path(Path(database_path))
        self._write_lock = Lock()

    @staticmethod
    def _format_utc(dt: datetime) -> str:
        utc_dt = dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def save_klines(self, bars: list[KLineBar]) -> int:
        """批量按 (品种, 周期, 来源, K 线时间) 增量写入：仅插入新柱或更新 OHLCV 发生变化的柱。"""
        if not bars:
            return 0
        deduped: dict[tuple[str, str, str, str], dict[str, object]] = {}
        for bar in bars:
            bar_time_utc = self._format_utc(bar.bar_time)
            key = (bar.product_code, bar.interval, bar.source, bar_time_utc)
            deduped[key] = {
                "product_code": bar.product_code,
                "interval": bar.interval,
                "source": bar.source,
                "bar_time_utc": bar_time_utc,
                "trade_date": bar.trade_date,
                "open_price": bar.open_price,
                "high_price": bar.high_price,
                "low_price": bar.low_price,
                "close_price": bar.close_price,
                "pre_close": bar.pre_close,
                "volume": bar.volume,
                "turnover": bar.turnover,
                "collected_at_utc": self._format_utc(bar.collected_at),
                "raw_json": bar.model_dump_json(),
            }
        groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
        for (product_code, interval, source, _bar_time), row in deduped.items():
            groups.setdefault((product_code, interval, source), []).append(row)

        to_write: list[dict[str, object]] = []
        with self._write_lock:
            with Session(self._engine) as read_session:
                for (product_code, interval, source), group_rows in groups.items():
                    times = [str(r["bar_time_utc"]) for r in group_rows]
                    existing_rows = read_session.execute(
                        select(
                            KLineBarRecord.bar_time_utc,
                            KLineBarRecord.trade_date,
                            KLineBarRecord.open_price,
                            KLineBarRecord.high_price,
                            KLineBarRecord.low_price,
                            KLineBarRecord.close_price,
                            KLineBarRecord.pre_close,
                            KLineBarRecord.volume,
                            KLineBarRecord.turnover,
                        ).where(
                            KLineBarRecord.product_code == product_code,
                            KLineBarRecord.interval == interval,
                            KLineBarRecord.source == source,
                            KLineBarRecord.bar_time_utc >= min(times),
                            KLineBarRecord.bar_time_utc <= max(times),
                        )
                    ).all()
                    existing = {
                        row[0]: (row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8])
                        for row in existing_rows
                    }
                    for row in group_rows:
                        fp = (
                            row["trade_date"],
                            row["open_price"],
                            row["high_price"],
                            row["low_price"],
                            row["close_price"],
                            row["pre_close"],
                            row["volume"],
                            row["turnover"],
                        )
                        if existing.get(str(row["bar_time_utc"])) != fp:
                            to_write.append(row)

            if not to_write:
                return 0

            with SQLiteDatabase.write_session(self._engine) as session:
                for idx in range(0, len(to_write), self._BATCH_SIZE):
                    chunk = to_write[idx : idx + self._BATCH_SIZE]
                    stmt = insert(KLineBarRecord).values(chunk)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["product_code", "interval", "source", "bar_time_utc"],
                        set_={
                            "trade_date": stmt.excluded.trade_date,
                            "open_price": stmt.excluded.open_price,
                            "high_price": stmt.excluded.high_price,
                            "low_price": stmt.excluded.low_price,
                            "close_price": stmt.excluded.close_price,
                            "pre_close": stmt.excluded.pre_close,
                            "volume": stmt.excluded.volume,
                            "turnover": stmt.excluded.turnover,
                            "collected_at_utc": stmt.excluded.collected_at_utc,
                            "raw_json": stmt.excluded.raw_json,
                        },
                    )
                    session.execute(stmt)
        return len(to_write)

    def save_time_sharing(self, points: list[TimeSharingPoint]) -> int:
        """批量按 (品种, 分时颗粒度, 来源, 交易时间) 增量写入：仅插入新点或更新价格/量额变化的点。"""
        if not points:
            return 0
        deduped: dict[tuple[str, str, str, str], dict[str, object]] = {}
        for point in points:
            trade_time_utc = self._format_utc(point.trade_time)
            key = (point.product_code, point.granularity, point.source, trade_time_utc)
            deduped[key] = {
                "product_code": point.product_code,
                "granularity": point.granularity,
                "source": point.source,
                "trade_time_utc": trade_time_utc,
                "show_time": point.show_time,
                "last_price": point.last_price,
                "pre_close": point.pre_close,
                "change_percent": point.change_percent,
                "trade_volume": point.trade_volume,
                "total_volume": point.total_volume,
                "turnover": point.turnover,
                "collected_at_utc": self._format_utc(point.collected_at),
                "raw_json": point.model_dump_json(),
            }
        groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
        for (product_code, granularity, source, _trade_time), row in deduped.items():
            groups.setdefault((product_code, granularity, source), []).append(row)

        to_write: list[dict[str, object]] = []
        with self._write_lock:
            with Session(self._engine) as read_session:
                for (product_code, granularity, source), group_rows in groups.items():
                    times = [str(r["trade_time_utc"]) for r in group_rows]
                    existing_rows = read_session.execute(
                        select(
                            TimeSharingRecord.trade_time_utc,
                            TimeSharingRecord.show_time,
                            TimeSharingRecord.last_price,
                            TimeSharingRecord.pre_close,
                            TimeSharingRecord.change_percent,
                            TimeSharingRecord.trade_volume,
                            TimeSharingRecord.total_volume,
                            TimeSharingRecord.turnover,
                        ).where(
                            TimeSharingRecord.product_code == product_code,
                            TimeSharingRecord.granularity == granularity,
                            TimeSharingRecord.source == source,
                            TimeSharingRecord.trade_time_utc >= min(times),
                            TimeSharingRecord.trade_time_utc <= max(times),
                        )
                    ).all()
                    existing = {
                        row[0]: (row[1], row[2], row[3], row[4], row[5], row[6], row[7]) for row in existing_rows
                    }
                    for row in group_rows:
                        fp = (
                            row["show_time"],
                            row["last_price"],
                            row["pre_close"],
                            row["change_percent"],
                            row["trade_volume"],
                            row["total_volume"],
                            row["turnover"],
                        )
                        if existing.get(str(row["trade_time_utc"])) != fp:
                            to_write.append(row)

            if not to_write:
                return 0

            with SQLiteDatabase.write_session(self._engine) as session:
                for idx in range(0, len(to_write), self._BATCH_SIZE):
                    chunk = to_write[idx : idx + self._BATCH_SIZE]
                    stmt = insert(TimeSharingRecord).values(chunk)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["product_code", "granularity", "source", "trade_time_utc"],
                        set_={
                            "show_time": stmt.excluded.show_time,
                            "last_price": stmt.excluded.last_price,
                            "pre_close": stmt.excluded.pre_close,
                            "change_percent": stmt.excluded.change_percent,
                            "trade_volume": stmt.excluded.trade_volume,
                            "total_volume": stmt.excluded.total_volume,
                            "turnover": stmt.excluded.turnover,
                            "collected_at_utc": stmt.excluded.collected_at_utc,
                            "raw_json": stmt.excluded.raw_json,
                        },
                    )
                    session.execute(stmt)
        return len(to_write)

    def query_klines(
        self,
        product_code: str,
        interval: str,
        *,
        source: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 500,
    ) -> list[KLineBar]:
        """按品种与周期查询已持久化的 K 线序列（按时间升序返回）。"""
        stmt = select(KLineBarRecord.raw_json).where(
            KLineBarRecord.product_code == product_code,
            KLineBarRecord.interval == interval,
        )
        if source is not None:
            stmt = stmt.where(KLineBarRecord.source == source)
        if start_time is not None:
            stmt = stmt.where(KLineBarRecord.bar_time_utc >= self._format_utc(start_time))
        if end_time is not None:
            stmt = stmt.where(KLineBarRecord.bar_time_utc <= self._format_utc(end_time))
        stmt = stmt.order_by(desc(KLineBarRecord.bar_time_utc), desc(KLineBarRecord.id)).limit(limit)
        with Session(self._engine) as session:
            rows = session.execute(stmt).scalars().all()
        items = [KLineBar.model_validate_json(raw) for raw in rows]
        items.reverse()
        return items

    def query_time_sharing(
        self,
        product_code: str,
        granularity: str = "m1",
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1500,
    ) -> list[TimeSharingPoint]:
        """按品种与颗粒度查询已持久化的分时走势序列（按时间升序返回）。"""
        stmt = select(TimeSharingRecord.raw_json).where(
            TimeSharingRecord.product_code == product_code,
            TimeSharingRecord.granularity == granularity,
        )
        if start_time is not None:
            stmt = stmt.where(TimeSharingRecord.trade_time_utc >= self._format_utc(start_time))
        if end_time is not None:
            stmt = stmt.where(TimeSharingRecord.trade_time_utc <= self._format_utc(end_time))
        stmt = stmt.order_by(desc(TimeSharingRecord.trade_time_utc), desc(TimeSharingRecord.id)).limit(limit)
        with Session(self._engine) as session:
            rows = session.execute(stmt).scalars().all()
        items = [TimeSharingPoint.model_validate_json(raw) for raw in rows]
        items.reverse()
        return items
