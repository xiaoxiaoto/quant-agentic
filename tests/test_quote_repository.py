"""时间点（PIT）行情仓储单元测试。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.repositories.quote_repository import QuoteRepository
from src.types.domain.quote import QuoteRaw


def test_quote_repository_save_and_pit_query(tmp_path: Path) -> None:
    repo = QuoteRepository(storage_dir=tmp_path)
    base_t = datetime(2026, 3, 2, 10, 0, 0, tzinfo=UTC)

    # 构造两条行情：一条在 10:00，另一条在 10:05
    q1 = QuoteRaw(
        quote_id="q1",
        source_id="src1",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=base_t,
        received_at=base_t + timedelta(seconds=1),
        available_at=base_t + timedelta(seconds=2),
        is_tradable=True,
    )
    q2 = QuoteRaw(
        quote_id="q2",
        source_id="src1",
        bid_price=601.0,
        ask_price=601.5,
        quote_at=base_t + timedelta(minutes=5),
        received_at=base_t + timedelta(minutes=5, seconds=1),
        available_at=base_t + timedelta(minutes=5, seconds=2),
        is_tradable=True,
    )

    repo.save_quote(q1)
    repo.save_quote(q2)

    # 在 10:01 时点（q2 尚未发生），获取最新 PIT 行情，必须只返回 q1
    as_of_1001 = base_t + timedelta(minutes=1)
    pit_quote = repo.get_latest_as_of(as_of_1001)
    assert pit_quote is not None
    assert pit_quote.quote_id == "q1"
    assert pit_quote.bid_price == 600.0

    # 在 10:06 时点，获取最新 PIT 行情，返回 q2
    as_of_1006 = base_t + timedelta(minutes=6)
    pit_quote_latest = repo.get_latest_as_of(as_of_1006)
    assert pit_quote_latest is not None
    assert pit_quote_latest.quote_id == "q2"


def test_query_pit_series_and_replay(tmp_path: Path) -> None:
    repo = QuoteRepository(storage_dir=tmp_path)
    base_t = datetime(2026, 3, 2, 10, 0, 0, tzinfo=UTC)

    quotes = [
        QuoteRaw(
            quote_id=f"q_{i}",
            source_id="src1",
            bid_price=600.0 + i,
            ask_price=600.5 + i,
            quote_at=base_t + timedelta(minutes=i),
            received_at=base_t + timedelta(minutes=i, seconds=1),
            available_at=base_t + timedelta(minutes=i, seconds=2),
            is_tradable=True,
        )
        for i in range(10)
    ]
    for q in quotes:
        repo.save_quote(q)

    # 检索 10:00 到 10:04 的行情，但在 10:03 时点做切片截断
    series = repo.query_pit_series(
        start_time=base_t,
        end_time=base_t + timedelta(minutes=4),
        as_of=base_t + timedelta(minutes=3, seconds=1),
    )
    # 满足 available_at <= 10:03:01 的只有 q_0, q_1, q_2 (q_3 的 available_at 是 10:03:02，被严格排除)
    assert len(series) == 3

    # 回放测试：时间覆盖到 10:02:05，使得 q_0, q_1, q_2 均达到 available_at
    replayed = list(
        repo.iter_replay(
            start_time=base_t,
            end_time=base_t + timedelta(minutes=2, seconds=5),
        )
    )
    assert len(replayed) == 3
    assert [q.quote_id for q in replayed] == ["q_0", "q_1", "q_2"]


def test_verify_file_integrity(tmp_path: Path) -> None:
    import hashlib

    repo = QuoteRepository(storage_dir=tmp_path)
    base_t = datetime(2026, 3, 2, 10, 0, 0, tzinfo=UTC)

    payload_hash = hashlib.sha256(b"q_valid:600.0:600.5").hexdigest()
    q_valid = QuoteRaw(
        quote_id="q_valid",
        source_id="src1",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=base_t,
        received_at=base_t,
        is_tradable=True,
        payload_hash=payload_hash,
    )
    q_corrupt = QuoteRaw(
        quote_id="q_bad",
        source_id="src1",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=base_t,
        received_at=base_t,
        is_tradable=True,
        payload_hash="invalid_hash_value",
    )

    repo.save_quote(q_valid)
    repo.save_quote(q_corrupt)

    file_path = tmp_path / f"quotes_{base_t.strftime('%Y-%m-%d')}.jsonl"
    valid, corrupt = repo.verify_file_integrity(file_path)
    assert valid == 1
    assert corrupt == 1
