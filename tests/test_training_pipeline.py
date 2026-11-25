"""PIT 训练样本构建、Walk-Forward 样本外滚动验证与 LightGBM 训练闭环测试。"""

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from main import app
from src.agents.lightgbm import LightGBMAgent
from src.configs.settings import Settings
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.reference_quote import ReferenceQuote
from src.contracts.enums.trading import DataQualityState, HoldingPeriod
from src.repositories.reference_quote import ReferenceQuoteRepository
from src.runtime.dependencies import RuntimeDependencies
from src.runtime.train import ModelTrainCommand


def _runtime(tmp_path: Path) -> RuntimeDependencies:
    return RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            timesfm_enabled=False,
            news_enabled=False,
            live_quotes_enabled=False,
        )
    )


def _seed_oscillating_quotes(runtime: RuntimeDependencies, count: int = 160) -> None:
    """生成带周期性涨跌的小时级双边报价，确保盈亏样本均衡。"""
    base = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    for i in range(count):
        ts = base + timedelta(hours=i)
        mid = 600.0 + 18.0 * math.sin(i / 6.0) + 0.08 * i
        runtime.quotes.save_quote(
            QuoteRaw(
                quote_id=f"q_train_{i:04d}",
                source_id="bank_hist",
                product_code="GOLD_ACCUMULATION",
                bid_price=round(mid - 0.3, 4),
                ask_price=round(mid + 0.3, 4),
                quote_at=ts,
                received_at=ts,
                available_at=ts,
                is_tradable=True,
                quality_state=DataQualityState.NORMAL,
            )
        )


def test_training_pipeline_end_to_end_walk_forward_and_export(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _seed_oscillating_quotes(runtime, count=180)

    samples_path = tmp_path / "out" / "pit_samples.jsonl"
    model_dir = tmp_path / "models" / "lgbm_v1"

    report = runtime.training_pipeline.run_pipeline(
        product_code="GOLD_ACCUMULATION",
        horizons=[HoldingPeriod.H1],
        n_splits=2,
        num_boost_round=15,
        confidence_level=0.85,
        export_samples_path=str(samples_path),
        output_model_dir=str(model_dir),
    )

    assert report.source_mode == "quotes"
    assert report.sample_counts["1h"] >= 150
    assert samples_path.is_file()
    assert len(samples_path.read_text(encoding="utf-8").strip().splitlines()) == report.sample_counts["1h"]

    # Walk-Forward 样本外验证应产出有效折次与汇总指标
    folds = report.walk_forward_folds["1h"]
    assert len(folds) >= 1
    assert "1h" in report.oos_summary
    assert 0.0 <= report.oos_summary["1h"]["avg_directional_accuracy"] <= 1.0
    assert 0.0 <= report.oos_summary["1h"]["avg_lcb_coverage"] <= 1.0

    # 最终产出的 LightGBM 模型必须可正常加载并推理
    assert report.trained_model_info is not None
    loaded = LightGBMAgent(model_dir)
    assert loaded.get_model_info()["model_version"] == "lightgbm_v1"


def test_training_pipeline_from_reference_quotes_api_and_cli(tmp_path: Path) -> None:
    db_path = tmp_path / "quant-agentic.sqlite3"
    ref_repo = ReferenceQuoteRepository(tmp_path / "references", database_path=db_path)
    base = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
    for i in range(160):
        ts = base + timedelta(hours=i)
        price = 750.0 + 30.0 * math.sin(i / 3.0)
        ref_repo.save(
            ReferenceQuote(
                quote_id=f"ref_t_{i:04d}",
                product_code="CMBC-JCJ",
                price=round(price, 4),
                unit="CNY/g",
                quote_at=ts,
                received_at=ts,
                available_at=ts,
                payload_hash=f"{i + 1:064x}",
            )
        )

    runtime = _runtime(tmp_path)
    app.state.runtime = runtime
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/models/train",
            json={
                "reference_product_code": "CMBC-JCJ",
                "spread_pct": 0.002,
                "horizons": ["1h"],
                "n_splits": 2,
                "num_boost_round": 12,
                "confidence_level": 0.85,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_mode"] == "reference"
        assert body["product_code"] == "CMBC-JCJ"
        assert body["sample_counts"]["1h"] >= 120
        assert len(body["walk_forward_folds"]["1h"]) >= 1
    finally:
        app.state.runtime = None

    # CLI 入口端到端验证
    code = ModelTrainCommand.run(
        ["--ref-product", "CMBC-JCJ", "--horizons", "1h", "--splits", "2", "--rounds", "12", "--confidence", "0.85"],
        settings=Settings(
            data_dir=str(tmp_path),
            timesfm_enabled=False,
            news_enabled=False,
            live_quotes_enabled=False,
        ),
    )
    assert code == 0
