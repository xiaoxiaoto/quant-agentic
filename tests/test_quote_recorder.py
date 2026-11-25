"""报价记录器单元测试。"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.contracts.enums.trading import DataQualityState
from src.services.market.quote_recorder import QuoteRecorder


class TestQuoteRecorder:
    def test_record_normal_quote_and_persist(self, tmp_path: Path) -> None:
        """合规报价成功记录、生成哈希并追加写入本地日志。"""
        recorder = QuoteRecorder(storage_dir=tmp_path)
        dt = datetime(2026, 11, 17, 10, 0, 0, tzinfo=UTC)

        quote, derived = recorder.record(
            quote_id="quote_001",
            source_id="mock_bank",
            bid_price=650.0,
            ask_price=650.6,
            quote_at=dt,
            is_tradable=True,
            raw_payload='{"bid":650.0,"ask":650.6}',
            now=dt,
        )

        assert quote.quality_state == DataQualityState.NORMAL
        assert quote.available_at == dt
        assert recorder.total_recorded == 1
        assert recorder.total_anomalies == 0
        assert recorder.latest_quote is not None
        assert recorder.latest_quote.quote_id == "quote_001"

        # 衍生指标校验
        assert derived.spread == pytest.approx(0.6)
        assert derived.mid_price == pytest.approx(650.3)
        assert derived.spread_pct == pytest.approx(0.6 / 650.3)

        # 检查统一落入 SQLite 且不额外生成本地 JSONL 文件
        from src.repositories.quote import QuoteRepository

        assert not (tmp_path / "quotes_2026-11-17.jsonl").exists()
        assert (tmp_path / "quant-agentic.sqlite3").exists()
        saved = QuoteRepository(tmp_path).get_latest_as_of(as_of=dt)
        assert saved is not None
        assert saved.quote_id == "quote_001"
        assert saved.payload_hash is not None

    def test_record_anomaly_does_not_update_latest(self, tmp_path: Path) -> None:
        """异常报价落入审计日志，但严禁更新最新可成交状态。"""
        recorder = QuoteRecorder(storage_dir=tmp_path)
        dt = datetime(2026, 11, 17, 10, 0, 0, tzinfo=UTC)

        # 倒挂报价
        quote, _ = recorder.record(
            quote_id="quote_bad",
            source_id="mock_bank",
            bid_price=651.0,
            ask_price=650.0,
            quote_at=dt,
            is_tradable=True,
            now=dt,
        )

        assert quote.quality_state == DataQualityState.ANOMALY
        assert quote.available_at is None
        assert recorder.total_recorded == 1
        assert recorder.total_anomalies == 1
        assert recorder.latest_quote is None

    def test_bank_basis_calculation(self, tmp_path: Path) -> None:
        """银行中间价相对国际参考价的基差率计算校验。"""
        recorder = QuoteRecorder(storage_dir=tmp_path)
        dt = datetime(2026, 11, 17, 10, 0, 0, tzinfo=UTC)

        # 国际参考价 650.0，银行中间价 651.0 -> 基差率 = +1.0 / 650.0
        _, derived = recorder.record(
            quote_id="quote_002",
            source_id="mock_bank",
            bid_price=650.8,
            ask_price=651.2,
            quote_at=dt,
            is_tradable=True,
            intl_ref_cny=650.0,
            now=dt,
        )

        assert derived.bank_basis is not None
        assert derived.bank_basis == pytest.approx(651.0 / 650.0 - 1.0)
