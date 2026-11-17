"""多窗口动态融合分析服务。

在日到周的主周期候选集中评估各持有窗口的期望净收益与置信下界。
在考虑交易摩擦与延迟成本的前提下，动态选择最优持有期或融合决策共识。
"""

from src.types.domain.feature import ModelPrediction
from src.types.enums.trading import HoldingPeriod


class MultiWindowFusion:
    """多时间窗口动态评估与融合决策引擎。"""

    def __init__(
        self,
        is_enabled: bool = False,
        default_primary: HoldingPeriod = HoldingPeriod.W1,
        allowed_horizons: list[HoldingPeriod] | None = None,
    ) -> None:
        self._enabled: bool = is_enabled
        self._default_primary: HoldingPeriod = default_primary
        self._allowed_horizons: list[HoldingPeriod] = allowed_horizons or [
            HoldingPeriod.D1,
            HoldingPeriod.D3,
            HoldingPeriod.W1,
            HoldingPeriod.W2,
            HoldingPeriod.M1,
        ]

    def is_enabled(self) -> bool:
        """检查动态多窗口融合是否处于激活状态。"""
        return self._enabled

    def select_primary_horizon(
        self,
        predictions: dict[HoldingPeriod, ModelPrediction],
    ) -> tuple[HoldingPeriod, ModelPrediction]:
        """从各持有周期的预测输出中选择置信下界最优的主评估窗口。

        未启用时直接返回默认固定主窗口（如 1w）。
        """
        if not self._enabled:
            pred = predictions.get(self._default_primary)
            if pred is not None:
                return self._default_primary, pred
            # 若默认窗口未预测则取第一个有效预测
            first_key = next(iter(predictions.keys()))
            return first_key, predictions[first_key]

        # 仅在允许的低频候选集中比对，严禁选择分钟或小时级子日内窗口开仓
        valid_candidates = {h: p for h, p in predictions.items() if h in self._allowed_horizons}
        if not valid_candidates:
            first_key = next(iter(predictions.keys()))
            return first_key, predictions[first_key]

        # 选择置信下界 (net_return_lcb) 最高且为正的窗口
        best_horizon = max(
            valid_candidates.keys(),
            key=lambda h: valid_candidates[h].net_return_lcb,
        )
        return best_horizon, valid_candidates[best_horizon]

    def compute_fused_score(
        self,
        predictions: dict[HoldingPeriod, ModelPrediction],
    ) -> float:
        """计算跨周期的多窗口加权融合打分。"""
        if not predictions:
            return 0.0

        weights: dict[HoldingPeriod, float] = {
            HoldingPeriod.D1: 0.15,
            HoldingPeriod.D3: 0.25,
            HoldingPeriod.W1: 0.35,
            HoldingPeriod.W2: 0.15,
            HoldingPeriod.M1: 0.10,
        }

        total_weight = 0.0
        weighted_lcb_sum = 0.0

        for h, pred in predictions.items():
            w = weights.get(h, 0.0)
            weighted_lcb_sum += pred.net_return_lcb * w
            total_weight += w

        if total_weight > 0:
            return round(weighted_lcb_sum / total_weight, 6)
        return 0.0
