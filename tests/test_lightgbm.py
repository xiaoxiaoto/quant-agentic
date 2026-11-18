"""真实 LightGBM 训练产物与时间隔离验证。"""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.agents.lightgbm import LightGBMAgent, TrainingSample
from src.contracts.domains.feature import FeatureSet
from src.contracts.enums.trading import HoldingPeriod
from src.services.prediction.predictor import ModelPredictor


def samples(horizon: HoldingPeriod = HoldingPeriod.H1) -> list[TrainingSample]:
    rng = np.random.default_rng(42)
    result = []
    for i in range(160):
        momentum = float(rng.uniform(-0.05, 0.05))
        timestamp = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=2 * i)
        result.append(
            TrainingSample(
                features=FeatureSet(
                    quote_id=f"q{i}",
                    as_of=timestamp,
                    return_1d=momentum,
                    spread_pct=0.002,
                    timesfm_forecast_return=momentum * 0.3,
                ),
                horizon=horizon,
                net_return=momentum * 0.8 - 0.004 + float(rng.normal(0, 0.002)),
                label_end_at=timestamp + timedelta(hours=1),
            )
        )
    return result


def test_real_training_roundtrip_and_predictor_dispatch(tmp_path: Path) -> None:
    rows = samples()
    LightGBMAgent.train(rows, tmp_path, num_boost_round=30)
    model = LightGBMAgent(tmp_path)
    timestamp = rows[-1].label_end_at + timedelta(hours=1)
    positive = FeatureSet(quote_id="positive", as_of=timestamp, return_1d=0.04, spread_pct=0.002)
    negative = positive.model_copy(update={"return_1d": -0.04})
    predictor = ModelPredictor(model=model)
    high = predictor.predict(positive, HoldingPeriod.H1)
    low = predictor.predict(negative, HoldingPeriod.H1)
    assert high.expected_net_return > low.expected_net_return
    assert high.win_probability > low.win_probability
    assert high.net_return_lcb <= high.expected_net_return
    assert high.model_version == "lightgbm_v1"
    assert high.is_calibrated
    info = model.get_model_info()
    assert info["production_approved"] is False
    assert info["horizons"]["1h"]["calibration_count"] == 40
    with pytest.raises(ValueError, match="没有"):
        predictor.predict(positive, HoldingPeriod.W1)
    with pytest.raises(ValueError, match="推理时间"):
        model.predict(rows[0].features, HoldingPeriod.H1)
    with pytest.raises(ValueError, match="非有限|有限"):
        model.predict(positive.model_copy(update={"return_1d": float("inf")}), HoldingPeriod.H1)


def test_training_purges_labels_crossing_calibration_boundary(tmp_path: Path) -> None:
    rows = samples()
    rows[119] = rows[119].model_copy(update={"label_end_at": rows[120].features.as_of})
    model = LightGBMAgent.train(rows, tmp_path, num_boost_round=3)
    metadata = model.get_model_info()["horizons"]["1h"]
    assert metadata["purged_count"] == 1
    assert datetime.fromisoformat(metadata["trained_until"]) < rows[120].features.as_of


def test_training_rejects_unordered_or_insufficient_samples(tmp_path: Path) -> None:
    rows = samples()
    with pytest.raises(ValueError, match="严格按时间"):
        LightGBMAgent.train(list(reversed(rows)), tmp_path)
    with pytest.raises(ValueError, match="至少需要"):
        LightGBMAgent.train(rows[:30], tmp_path)
    with pytest.raises(ValueError, match="盈利及亏损"):
        LightGBMAgent.train([row.model_copy(update={"net_return": 0.01}) for row in rows], tmp_path)


def test_training_label_time_validation() -> None:
    row = samples()[0]
    with pytest.raises(ValueError, match="结束时间"):
        TrainingSample.model_validate({**row.model_dump(), "label_end_at": row.features.as_of})
    with pytest.raises(ValueError, match="时区"):
        TrainingSample.model_validate({**row.model_dump(), "label_end_at": datetime(2025, 2, 1)})
    with pytest.raises(ValueError):
        TrainingSample.model_validate({**row.model_dump(), "net_return": float("nan")})
    with pytest.raises(ValueError, match="持有期"):
        TrainingSample.model_validate({**row.model_dump(), "horizon": "1w"})


def test_training_cli_writes_real_artifact(tmp_path: Path) -> None:
    source = tmp_path / "samples.jsonl"
    source.write_text("\n".join(row.model_dump_json() for row in samples()), encoding="utf-8")
    output = tmp_path / "model"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.agents.lightgbm",
            "--input",
            str(source),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert json.loads(result.stdout)["production_approved"] is False
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert "Tree=" in manifest["horizons"]["1h"]["regressor"]
    manifest["horizons"]["1h"]["residual_bound"] = -0.5
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="校准参数"):
        LightGBMAgent(output)
