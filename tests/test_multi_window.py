"""多窗口动态融合单元测试。"""

from src.services.multi_window import MultiWindowFusion
from src.types.domain.feature import ModelPrediction
from src.types.enums.trading import HoldingPeriod


def test_multi_window_disabled_defaults_to_primary() -> None:
    fusion = MultiWindowFusion(is_enabled=False, default_primary=HoldingPeriod.W1)

    preds = {
        HoldingPeriod.D1: ModelPrediction(
            horizon=HoldingPeriod.D1,
            expected_net_return=0.01,
            net_return_lcb=0.005,
            win_probability=0.6,
        ),
        HoldingPeriod.W1: ModelPrediction(
            horizon=HoldingPeriod.W1,
            expected_net_return=0.02,
            net_return_lcb=0.012,
            win_probability=0.7,
        ),
    }

    horizon, selected = fusion.select_primary_horizon(preds)
    assert horizon == HoldingPeriod.W1
    assert selected.net_return_lcb == 0.012


def test_multi_window_enabled_selects_optimal_lcb() -> None:
    fusion = MultiWindowFusion(is_enabled=True)

    preds = {
        HoldingPeriod.D1: ModelPrediction(
            horizon=HoldingPeriod.D1,
            expected_net_return=0.008,
            net_return_lcb=0.002,
            win_probability=0.55,
        ),
        HoldingPeriod.W1: ModelPrediction(
            horizon=HoldingPeriod.W1,
            expected_net_return=0.025,
            net_return_lcb=0.015,  # 最高 LCB
            win_probability=0.72,
        ),
        HoldingPeriod.M1: ModelPrediction(
            horizon=HoldingPeriod.M1,
            expected_net_return=0.020,
            net_return_lcb=0.009,
            win_probability=0.65,
        ),
    }

    horizon, selected = fusion.select_primary_horizon(preds)
    assert horizon == HoldingPeriod.W1
    assert selected.net_return_lcb == 0.015

    score = fusion.compute_fused_score(preds)
    assert score > 0.0
