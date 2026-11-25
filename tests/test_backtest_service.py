"""回测编排服务单元测试。"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState
from src.repositories.quote import QuoteRepository
from src.services.decision.decision_engine import DecisionEngine
from src.services.execution.backtest_service import BacktestService
from src.services.prediction.feature_engine import FeatureEngine
from src.services.prediction.predictor import ModelPredictor
from src.services.risk.risk_engine import RiskEngine


def _make_quotes(count: int = 12, start_price: float = 600.0) -> list[QuoteRaw]:
    base = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    quotes: list[QuoteRaw] = []
    for idx in range(count):
        ts = base + timedelta(hours=idx)
        bid = round(start_price + idx * 1.5, 4)
        ask = round(bid + 0.3, 4)
        quotes.append(
            QuoteRaw(
                quote_id=f"bt_q_{idx}",
                source_id="test_bank",
                bid_price=bid,
                ask_price=ask,
                quote_at=ts,
                received_at=ts,
                available_at=ts,
                is_tradable=True,
                quality_state=DataQualityState.NORMAL,
                product_code="GOLD_ACCUMULATION",
            )
        )
    return quotes


def _build_service(tmp_path: Path) -> tuple[BacktestService, QuoteRepository]:
    fee_rule = FeeRule()
    repo = QuoteRepository(storage_dir=tmp_path)
    service = BacktestService(
        quotes=repo,
        feature_engine=FeatureEngine(),
        predictor=ModelPredictor(fee_rule=fee_rule),
        decision_engine=DecisionEngine(fee_rule=fee_rule),
        risk_engine=RiskEngine(fee_rule=fee_rule),
        fee_rule=fee_rule,
    )
    return service, repo


def test_backtest_from_repository(tmp_path: Path) -> None:
    service, repo = _build_service(tmp_path)
    quotes = _make_quotes(15)
    for item in quotes:
        repo.save_quote(item)

    summary = service.run(
        product_code="GOLD_ACCUMULATION",
        start_time=quotes[0].quote_at,
        end_time=quotes[-1].quote_at,
        initial_cash=50000.0,
        trade_grams_per_order=2.0,
    )

    assert summary.product_code == "GOLD_ACCUMULATION"
    assert summary.quote_count == 15
    assert summary.shadow_report.initial_cash == 50000.0
    assert len(summary.baseline_report.metrics) == 3
    assert summary.excess_return_vs_buy_and_hold is not None
    assert summary.excess_return_vs_dca is not None


def test_backtest_from_jsonl_file_and_empty_repo(tmp_path: Path) -> None:
    service, _ = _build_service(tmp_path)
    empty = service.run(product_code="GOLD_ACCUMULATION")
    assert empty.quote_count == 0
    assert empty.shadow_report.final_equity == 100000.0

    quotes = _make_quotes(8)
    jsonl_path = tmp_path / "offline_quotes.jsonl"
    jsonl_path.write_text(
        "\n".join(json.dumps(q.model_dump(mode="json"), ensure_ascii=False) for q in quotes) + "\n",
        encoding="utf-8",
    )
    summary = service.run(quotes_file=jsonl_path, product_code="GOLD_ACCUMULATION")
    assert summary.quote_count == 8

    with pytest.raises(FileNotFoundError):
        service.run(quotes_file=tmp_path / "missing.jsonl")
