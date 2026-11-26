"""Qlib 本地 SQLite 数据导出与外部目录校验单元测试。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState
from src.contracts.exceptions import QlibDatasetError
from src.contracts.schemas.qlib import QlibExportRequest
from src.repositories.quote import QuoteRepository
from src.services.market.session_calendar import SessionCalendar
from src.services.qlib.data_exporter import QlibDataExporter


def _make_quote(idx: int, dt: datetime, price: float) -> QuoteRaw:
    return QuoteRaw(
        quote_id=f"q_{idx}",
        source_id="CMBC",
        product_code="GOLD_ACCUMULATION",
        bid_price=round(price - 0.5, 4),
        ask_price=round(price + 0.5, 4),
        quote_at=dt,
        received_at=dt,
        available_at=dt,
        is_tradable=True,
        is_synthetic=False,
        payload_hash=f"hash_{idx}",
        quality_state=DataQualityState.NORMAL,
    )


def test_export_quotes_to_qlib_bin_and_filter_weekend(tmp_path: Path) -> None:
    """双边报价导出应生成标准小端 float32 bin 文件，并在 1h 频过滤周末休市柱。"""
    repo = QuoteRepository(storage_dir=tmp_path / "quotes", database_path=tmp_path / "test.sqlite3")
    # 2026-11-23 为周一 UTC 02:00（北京时间 10:00，交易时段）
    monday = datetime(2026, 11, 23, 2, 0, tzinfo=UTC)
    # 2026-11-22 为周日 UTC 02:00（北京时间 10:00，休市时段）
    sunday = datetime(2026, 11, 22, 2, 0, tzinfo=UTC)

    repo.save_quote(_make_quote(0, sunday, 600.0))
    for i in range(5):
        repo.save_quote(_make_quote(i + 1, monday + timedelta(hours=i), 610.0 + i * 2.0))

    exporter = QlibDataExporter(
        quotes=repo,
        calendar=SessionCalendar(),
        fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.004),
    )
    out_dir = tmp_path / "qlib_out"
    report = exporter.export_local_dataset(
        QlibExportRequest(source_mode="quotes", instruments=["GOLD_ACCUMULATION"], freq="1h"),
        target_dir=out_dir,
    )

    assert report.manifest.calendar_count == 5
    assert report.manifest.instruments == ["GOLD_ACCUMULATION"]
    assert "bid" in report.manifest.fields
    assert "ask" in report.manifest.fields
    assert (out_dir / "calendars" / "60min.txt").exists()
    assert (out_dir / "instruments" / "all.txt").exists()

    close_bin = out_dir / "features" / "gold_accumulation" / "close.60min.bin"
    arr = np.fromfile(close_bin, dtype="<f")
    assert arr[0] == pytest.approx(0.0)
    assert len(arr) == 6
    assert arr[1] == pytest.approx(610.0, rel=1e-4)
    assert arr[-1] == pytest.approx(618.0, rel=1e-4)

    inspected = exporter.inspect_dataset(out_dir, source_type="local_sqlite")
    assert inspected is not None
    assert inspected.calendar_count == 5


def test_inspect_external_qlib_directory(tmp_path: Path) -> None:
    """外部标准 Qlib 目录无 dataset_manifest.json 时仍应能正确解析日历、标的与字段。"""
    ext_dir = tmp_path / "cn_data"
    (ext_dir / "calendars").mkdir(parents=True)
    (ext_dir / "instruments").mkdir(parents=True)
    (ext_dir / "features" / "sh518880").mkdir(parents=True)
    (ext_dir / "calendars" / "day.txt").write_text("2026-01-02\n2026-01-03\n", encoding="utf-8")
    (ext_dir / "instruments" / "all.txt").write_text("SH518880\t2026-01-02\t2026-01-03\n", encoding="utf-8")
    np.array([0.0, 5.1, 5.2], dtype="<f").tofile(ext_dir / "features" / "sh518880" / "close.day.bin")

    repo = QuoteRepository(storage_dir=tmp_path / "quotes", database_path=tmp_path / "test.sqlite3")
    exporter = QlibDataExporter(quotes=repo, calendar=SessionCalendar())
    manifest = exporter.inspect_dataset(ext_dir, source_type="external_qlib")

    assert manifest is not None
    assert manifest.source_type == "external_qlib"
    assert manifest.freq == "day"
    assert manifest.instruments == ["SH518880"]
    assert "close" in manifest.fields
    assert manifest.calendar_count == 2


def test_export_empty_quotes_raises_dataset_error(tmp_path: Path) -> None:
    """无有效历史数据时应抛出 QlibDatasetError。"""
    repo = QuoteRepository(storage_dir=tmp_path / "quotes", database_path=tmp_path / "test.sqlite3")
    exporter = QlibDataExporter(quotes=repo, calendar=SessionCalendar())
    with pytest.raises(QlibDatasetError):
        exporter.export_local_dataset(
            QlibExportRequest(source_mode="quotes", instruments=["GOLD_ACCUMULATION"], freq="1h"),
            target_dir=tmp_path / "qlib_empty",
        )
