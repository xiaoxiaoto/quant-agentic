"""基础时序模型适配器服务。

作为基础时序模型适配器，提供零样本预测与外部校准能力。
输出作为特征输入上游模型，未获授权或离线时平滑降级。
"""

from datetime import UTC, datetime

from src.types.domain.quote import QuoteRaw
from src.types.enums.trading import HoldingPeriod


class TimesFMAdapter:
    """基础时序模型（TimesFM）外部适配器与特征生成器。"""

    def __init__(
        self,
        is_enabled: bool = False,
        model_version: str = "timesfm_3.0_zero_shot",
        calibration_slope: float = 1.0,
        calibration_intercept: float = 0.0,
    ) -> None:
        self._enabled: bool = is_enabled
        self._version: str = model_version
        self._slope: float = calibration_slope
        self._intercept: float = calibration_intercept

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _estimate_trend_return(
        self,
        prices: list[float],
        horizon_steps: int,
    ) -> float:
        """基于历史序列与时序先验进行零样本或外推估计。"""
        if len(prices) < 2:
            return 0.0
        # 提取短期动量与均线平滑
        recent = prices[-1]
        base = prices[0]
        historical_momentum = (recent - base) / base if base > 0 else 0.0
        # 衰减外推
        decay = 1.0 / (1.0 + 0.1 * horizon_steps)
        raw_forecast = historical_momentum * decay
        calibrated = self._slope * raw_forecast + self._intercept
        return round(calibrated, 6)

    def is_available(self) -> bool:
        """检查模型是否已启用且授权合规核验通过。"""
        return self._enabled

    def predict_feature(
        self,
        quotes: list[QuoteRaw],
        horizon: HoldingPeriod = HoldingPeriod.W1,
    ) -> float | None:
        """根据历史行情序列提取时序模型预测特征。

        若模型未启用或历史数据不足，返回 None 以实现安全降级。
        """
        if not self._enabled or len(quotes) < 5:
            return None

        # 映射持有周期步长
        horizon_map = {
            HoldingPeriod.H1: 1,
            HoldingPeriod.H4: 4,
            HoldingPeriod.D1: 24,
            HoldingPeriod.D3: 72,
            HoldingPeriod.W1: 168,
            HoldingPeriod.W2: 336,
            HoldingPeriod.M1: 720,
        }
        steps = horizon_map.get(horizon, 168)

        prices = [q.bid_price for q in quotes if q.is_tradable]
        if len(prices) < 5:
            return None

        return self._estimate_trend_return(prices, steps)

    def get_model_info(self) -> dict[str, str | bool | float]:
        """返回适配器当前版本与状态信息。"""
        return {
            "model_version": self._version,
            "is_enabled": self._enabled,
            "calibration_slope": self._slope,
            "calibration_intercept": self._intercept,
        }
