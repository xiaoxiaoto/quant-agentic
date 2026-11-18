"""核心模型预测服务。

配置真实模型时使用 LightGBM；未配置时保留明确标识的启发式代理。
严格以扣除交易与退出成本后的期望净收益下界作为交易决策依据。
"""

import math
from statistics import NormalDist

from src.agents.lightgbm import LightGBMAgent
from src.contracts.domains.feature import FeatureSet, ModelPrediction
from src.contracts.domains.product import FeeRule
from src.contracts.enums.trading import HoldingPeriod
from src.services.decision.cost_calculator import CostCalculator


class ModelPredictor:
    """优先使用按持有期训练的模型，兼容未配置模型的研究原型。"""

    def __init__(
        self,
        confidence_level: float = 0.90,
        base_uncertainty: float = 0.008,
        fee_rule: FeeRule | None = None,
        model: LightGBMAgent | None = None,
    ) -> None:
        self._model = model
        self._cost = CostCalculator(fee_rule)
        self._base_uncertainty = base_uncertainty
        if not 0.5 < confidence_level < 1.0 or not math.isfinite(base_uncertainty) or base_uncertainty <= 0:
            raise ValueError("单侧置信水平须介于0.5与1之间，不确定性须为有限正数")
        self._z_score = NormalDist().inv_cdf(confidence_level)

    def _sigmoid(self, x: float) -> float:
        return 1.0 / (1.0 + math.exp(-max(min(x, 20.0), -20.0)))

    def predict(
        self,
        features: FeatureSet,
        horizon: HoldingPeriod = HoldingPeriod.W1,
    ) -> ModelPrediction:
        """根据输入特征综合估计目标持有周期的净收益期望与置信下界。"""
        if self._model is not None:
            return self._model.predict(features, horizon)
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

        # 将中间价收益转换为含双边点差与手续费的实际往返收益。
        half_spread = features.spread_pct / 2.0
        raw_net_return = self._cost.net_return(
            entry_ask=1.0 + half_spread,
            exit_bid=(1.0 - half_spread) * (1.0 + raw_net_return),
        )

        # 动态估计不确定性
        uncertainty = self._base_uncertainty * vol_factor
        if features.event_risk_flag_count > 0:
            uncertainty *= 1.5

        # 计算置信区间下界 LCB = E[r] - z * sigma
        lcb = raw_net_return - self._z_score * uncertainty

        # 基于标准化打分的胜率代理，未经样本外校准
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
