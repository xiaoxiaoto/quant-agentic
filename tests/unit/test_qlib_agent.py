"""QlibAgent 产物加载、PIT 边界校验与 ModelPredictor 桥接单元测试。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.agents.qlib import QlibAgent
from src.contracts.domains.feature import FeatureSet
from src.contracts.enums.trading import HoldingPeriod
from src.services.prediction.predictor import ModelPredictor


def _save_linear_artifact(output_dir: Path, *, serving_compatible: bool = True) -> Path:
    rng = np.random.default_rng(7)
    cal_pred = rng.normal(0.005, 0.01, size=30)
    cal_actual = cal_pred + rng.normal(0.0, 0.004, size=30)
    trained_until = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    calibrated_until = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
    features = ["return_1d", "return_3d", "spread_pct"]
    entry = QlibAgent.fit_calibration_entry(
        cal_predicted=cal_pred,
        cal_raw_scores=cal_pred,
        cal_actual=cal_actual,
        confidence_level=0.9,
        training_count=100,
        purged_count=5,
        trained_until=trained_until,
        calibrated_until=calibrated_until,
        extra_payload={
            "weights": [0.4, 0.2, -0.3],
            "intercept": 0.002,
            "feature_means": [0.0, 0.0, 0.002],
            "feature_stds": [0.01, 0.02, 0.001],
        },
    )
    return QlibAgent.save_artifact(
        output_dir,
        model_type="linear",
        features=features,
        horizons={HoldingPeriod.D1.value: entry},
        experiment_name="unit_test_exp",
        run_id="qlib_unit001",
        source_type="local_sqlite",
        factor_preset="gold_core",
        serving_compatible=serving_compatible,
    )


def test_qlib_agent_predict_and_predictor_bridge(tmp_path: Path) -> None:
    """QlibAgent 应通过 NetReturnModelProtocol 无缝接入 ModelPredictor 并输出校准预测。"""
    model_dir = _save_linear_artifact(tmp_path / "qlib_linear_v1", serving_compatible=True)
    agent = QlibAgent(model_dir)
    predictor = ModelPredictor(model=agent)

    features = FeatureSet(
        quote_id="q_live",
        as_of=datetime(2026, 11, 15, 0, 0, tzinfo=UTC),
        return_1d=0.012,
        return_3d=0.02,
        spread_pct=0.0018,
    )
    pred = predictor.predict(features, horizon=HoldingPeriod.D1)

    assert pred.model_version == "qlib_v1"
    assert pred.is_calibrated is True
    assert pred.net_return_lcb <= pred.expected_net_return
    assert 0.0 <= pred.win_probability <= 1.0
    assert set(pred.feature_importance.keys()) == {"return_1d", "return_3d", "spread_pct"}
    assert agent.get_model_info()["run_id"] == "qlib_unit001"


def test_qlib_agent_rejects_pit_leakage_and_offline_artifact(tmp_path: Path) -> None:
    """推理时间早于校准边界或加载非线上兼容产物时必须抛出 ValueError。"""
    model_dir = _save_linear_artifact(tmp_path / "qlib_pit", serving_compatible=True)
    agent = QlibAgent(model_dir)
    past_features = FeatureSet(
        quote_id="q_past",
        as_of=datetime(2026, 10, 15, 0, 0, tzinfo=UTC) - timedelta(days=1),
        return_1d=0.01,
        return_3d=0.01,
        spread_pct=0.002,
    )
    with pytest.raises(ValueError, match="校准标签结束时间"):
        agent.predict(past_features, horizon=HoldingPeriod.D1)

    offline_dir = _save_linear_artifact(tmp_path / "qlib_offline", serving_compatible=False)
    with pytest.raises(ValueError, match="离线研究模型"):
        QlibAgent(offline_dir)
