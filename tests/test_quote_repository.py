"""时间点（PIT）行情仓储单元测试。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.contracts.domains.quote import QuoteRaw
from src.repositories.quote import QuoteRepository


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

    file_path = tmp_path / f"quotes_{base_t.strftime('%Y-%m-%d')}.jsonl"
    import json

    file_path.write_text(
        "\n".join(json.dumps(q.model_dump(mode="json", exclude={"record_hash"})) for q in (q_valid, q_corrupt)) + "\n"
    )
    valid, corrupt = repo.verify_file_integrity(file_path)
    assert valid == 1
    assert corrupt == 1


def test_latest_excludes_rejected_quote(tmp_path: Path) -> None:
    from src.services.market.quote_recorder import QuoteRecorder

    now = datetime(2026, 3, 2, tzinfo=UTC)
    recorder = QuoteRecorder(tmp_path)
    recorder.record("good", "s", 600.0, 600.5, now, True, now=now)
    recorder.record("bad", "s", -1.0, 600.5, now, True, now=now)
    latest = QuoteRepository(tmp_path).get_latest_as_of(now)
    assert latest is not None
    assert latest.quote_id == "good"


def test_recorder_hash_verifies_all_record_fields(tmp_path: Path) -> None:
    import json

    from src.services.market.quote_recorder import QuoteRecorder

    now = datetime(2026, 3, 2, tzinfo=UTC)
    repo = QuoteRepository(tmp_path, write_jsonl=True)
    QuoteRecorder(tmp_path, repository=repo).record(
        "q", "s", 600.0, 600.5, now, True, raw_payload='{"bid":600}', now=now
    )
    path = next(tmp_path.glob("*.jsonl"))
    assert repo.verify_file_integrity(path) == (1, 0)
    record = json.loads(path.read_text())
    record["is_tradable"] = False
    path.write_text(json.dumps(record) + "\n")
    assert repo.verify_file_integrity(path) == (0, 1)


def test_pit_excludes_future_source_timestamp(tmp_path: Path) -> None:
    now = datetime(2026, 3, 2, tzinfo=UTC)
    quote = QuoteRaw(
        quote_id="future",
        source_id="s",
        bid_price=600,
        ask_price=601,
        quote_at=now + timedelta(hours=1),
        received_at=now,
        available_at=now,
        is_tradable=True,
    )
    repo = QuoteRepository(tmp_path)
    repo.save_quote(quote)
    assert repo.get_latest_as_of(now) is None
    assert repo.query_pit_series(now, now + timedelta(hours=1), as_of=now) == []


def test_late_old_quote_does_not_replace_latest_source_quote(tmp_path: Path) -> None:
    now = datetime(2026, 3, 2, tzinfo=UTC)
    newest = QuoteRaw(
        quote_id="newest",
        source_id="s",
        bid_price=610,
        ask_price=611,
        quote_at=now,
        received_at=now,
        available_at=now,
        is_tradable=True,
    )
    late_old = newest.model_copy(
        update={
            "quote_id": "old",
            "quote_at": now - timedelta(hours=1),
            "received_at": now + timedelta(minutes=1),
            "available_at": now + timedelta(minutes=1),
        }
    )
    repo = QuoteRepository(tmp_path)
    repo.save_quote(newest)
    repo.save_quote(late_old)
    latest = repo.get_latest_as_of(now + timedelta(minutes=2))
    assert latest is not None
    assert latest.quote_id == "newest"
    replay = list(repo.iter_replay(now - timedelta(hours=2), now + timedelta(minutes=2)))
    assert [q.quote_id for q in replay] == ["newest", "old"]


def test_pit_does_not_use_availability_before_received_at(tmp_path: Path) -> None:
    now = datetime(2026, 3, 2, tzinfo=UTC)
    quote = QuoteRaw(
        quote_id="delayed",
        source_id="s",
        bid_price=600,
        ask_price=601,
        quote_at=now,
        received_at=now + timedelta(minutes=1),
        available_at=now,
        is_tradable=True,
    )
    repo = QuoteRepository(tmp_path)
    repo.save_quote(quote)
    assert repo.get_latest_as_of(now) is None


def test_verify_file_integrity_ignores_blank_lines(tmp_path: Path) -> None:
    now = datetime(2026, 3, 2, tzinfo=UTC)
    repo = QuoteRepository(tmp_path, write_jsonl=True)
    repo.save_quote(
        QuoteRaw(
            quote_id="ok_1",
            source_id="s",
            bid_price=600.0,
            ask_price=600.5,
            quote_at=now,
            received_at=now,
            available_at=now,
            is_tradable=True,
        )
    )
    path = next(tmp_path.glob("quotes_*.jsonl"))
    path.write_text("\n" + path.read_text(encoding="utf-8") + "\n   \n", encoding="utf-8")
    assert repo.verify_file_integrity(path) == (1, 0)


def test_mixed_naive_and_aware_timestamps_do_not_crash(tmp_path: Path) -> None:
    repo = QuoteRepository(tmp_path, write_jsonl=True)
    naive_quote = QuoteRaw(
        quote_id="legacy_naive",
        source_id="s",
        bid_price=600.0,
        ask_price=600.5,
        quote_at=datetime(2026, 11, 18, 10, 0, 0),
        received_at=datetime(2026, 11, 18, 10, 0, 1, tzinfo=UTC),
        available_at=datetime(2026, 11, 18, 10, 0, 1, tzinfo=UTC),
        is_tradable=True,
    )
    repo.save_quote(naive_quote)

    as_of = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
    latest = repo.get_latest_as_of(as_of=as_of)
    assert latest is not None
    assert latest.quote_id == "legacy_naive"
    assert latest.quote_at.tzinfo == UTC

    series = repo.query_pit_series(
        start_time=datetime(2026, 11, 18, 0, 0, 0, tzinfo=UTC),
        end_time=as_of,
        as_of=as_of,
    )
    assert [item.quote_id for item in series] == ["legacy_naive"]

    path = next(tmp_path.glob("quotes_*.jsonl"))
    assert repo.verify_file_integrity(path) == (1, 0)
