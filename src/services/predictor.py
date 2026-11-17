"""核心模型预测服务。

实现基于特征输入的净收益率点预测、置信区间下界（LCB）估计与校准胜率。
严格以扣除交易与退出成本后的期望净收益下界作为交易决策依据。
"""

import math

from src.types.domain.feature import FeatureSet, ModelPrediction
from src.types.enums.trading import HoldingPeriod


class ModelPredictor:
    """核心模型预测引擎。"""

    def __init__(
        self,
        confidence_level: float = 0.90,
        base_uncertainty: float = 0.008,
    ) -> None:
        self._confidence_level = confidence_level
        self._base_uncertainty = base_uncertainty
        # 90% 置信区间单侧分位数 z ≈ 1.282，95% ≈ 1.645
        self._z_score = 1.282 if confidence_level <= 0.90 else 1.645

    def _sigmoid(self, x: float) -> float:
        return 1.0 / (1.0 + math.exp(-max(min(x, 20.0), -20.0)))

    def predict(
        self,
        features: FeatureSet,
        horizon: HoldingPeriod = HoldingPeriod.W1,
    ) -> ModelPrediction:
        """根据输入特征综合估计目标持有周期的净收益期望与置信下界。"""
        # 1. 提取各特征因子打分
        mom_score = 0.0
        if features.return_1w is not None:
            mom_score += features.return_1w * 0.4
        if features.return_3d is not None:
            mom_score += features.return_3d * 0.3
        if features.return_1d is not None:
            mom_score += features.return_1d * 0.3

        # 时序模型增量特征注入
        timesfm_contrib = 0.0
        if features.timesfm_forecast_return is not None:
            timesfm_contrib = features.timesfm_forecast_return * 0.25

        # 基差均值回归因子（基差过高倾向于下跌回调，基差过低倾向于上涨修复）
        basis_contrib = 0.0
        if features.bank_basis is not None:
            basis_contrib = -features.bank_basis * 0.15

        # 波动率对不确定性与预期下界的影响
        vol_factor = 1.0 + min(features.realized_volatility, 0.5)

        # 粗预测净收益
        raw_net_return = mom_score * 0.6 + timesfm_contrib + basis_contrib

        # 点差成本扣减
        raw_net_return -= features.spread_pct

        # 动态估计不确定性
        uncertainty = self._base_uncertainty * vol_factor
        if features.event_risk_flag_count > 0:
            uncertainty *= 1.5

        # 计算置信区间下界 LCB = E[r] - z * sigma
        lcb = raw_net_return - self._z_score * uncertainty

        # 校准胜率计算 (基于标准正态累积分布的 logit 近似)
        z_win = raw_net_return / uncertainty if uncertainty > 0 else 0.0
        win_prob = self._sigmoid(z_win * 1.5)

        feature_imp = {
            "momentum_trend": 0.45,
            "timesfm": 0.25 if features.timesfm_forecast_return is not None else 0.0,
            "bank_basis": 0.15 if features.bank_basis is not None else 0.0,
            "spread_cost": 0.15,
        }

        return ModelPrediction(
            horizon=horizon,
            expected_net_return=round(raw_net_return, 6),
            net_return_lcb=round(lcb, 6),
            win_probability=round(win_prob, 4),
            uncertainty=round(uncertainty, 6),
            feature_importance=feature_imp,
        )
