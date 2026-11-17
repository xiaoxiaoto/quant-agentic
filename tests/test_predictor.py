"""核心模型预测器单元测试。"""

from datetime import UTC, datetime

from src.services.predictor import ModelPredictor
from src.types.domain.feature import FeatureSet
from src.types.enums.trading import HoldingPeriod


def test_predictor_confidence_bound() -> None:
    predictor = ModelPredictor(confidence_level=0.90, base_uncertainty=0.005)
    now_dt = datetime.now(UTC)

    # 强上涨特征输入
    feat_bull = FeatureSet(
        quote_id="q_bull",
        as_of=now_dt,
        return_1d=0.015,
        return_3d=0.025,
        return_1w=0.035,
        realized_volatility=0.12,
        spread=0.5,
        spread_pct=0.0008,
        timesfm_forecast_return=0.02,
    )

    pred = predictor.predict(feat_bull, horizon=HoldingPeriod.W1)
    assert pred.expected_net_return > 0.0
    # 核心约束：LCB 必须严格等于 expected_net_return - z * uncertainty
    expected_lcb = pred.expected_net_return - 1.282 * pred.uncertainty
    assert abs(pred.net_return_lcb - expected_lcb) < 1e-5
    assert pred.win_probability > 0.5


def test_predictor_risk_widens_uncertainty() -> None:
    predictor = ModelPredictor(base_uncertainty=0.005)
    now_dt = datetime.now(UTC)

    feat_normal = FeatureSet(
        quote_id="q1",
        as_of=now_dt,
        return_1w=0.02,
        event_risk_flag_count=0,
    )
    feat_high_risk = FeatureSet(
        quote_id="q2",
        as_of=now_dt,
        return_1w=0.02,
        event_risk_flag_count=2,  # 存在高危事件标志
    )

    pred_norm = predictor.predict(feat_normal)
    pred_risk = predictor.predict(feat_high_risk)

    assert pred_risk.uncertainty > pred_norm.uncertainty
    # 高不确定性压低了置信下界
    assert pred_risk.net_return_lcb < pred_norm.net_return_lcb
