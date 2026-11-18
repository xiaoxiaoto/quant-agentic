"""TimesFM 3.0 预测 Agent，使用真实权重的懒加载推理。

使用小时等频历史报价预测价格，再转换为收益特征；失败时返回缺失值。
下载权重的使用范围受其独立许可证约束，启用不代表已获生产授权。
"""

from __future__ import annotations

import importlib
import logging
import math
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any, ClassVar

import numpy as np

from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState, HoldingPeriod


class TimesFMAgent:
    """基于 TimesFM 基础时间序列模型的时序预测 Agent。"""

    _HORIZON_HOURS: ClassVar[dict[HoldingPeriod, int]] = {
        HoldingPeriod.H1: 1,
        HoldingPeriod.H4: 4,
        HoldingPeriod.D1: 24,
        HoldingPeriod.D3: 72,
        HoldingPeriod.W1: 168,
        HoldingPeriod.W2: 336,
        HoldingPeriod.M1: 720,
    }

    def __init__(
        self,
        is_enabled: bool = False,
        model_version: str = "timesfm_3.0",
        calibration_slope: float = 1.0,
        calibration_intercept: float = 0.0,
        checkpoint_path: str = "google/timesfm-3.0-pytorch",
        device: str = "cpu",
        context_length: int = 512,
        min_context: int = 32,
        evaluator: Any = None,
    ) -> None:
        if not 2 <= min_context <= context_length <= 15360:
            raise ValueError("上下文长度须满足 2 <= 最少样本数 <= 上下文长度 <= 15360")
        if not all(math.isfinite(value) for value in (calibration_slope, calibration_intercept)):
            raise ValueError("校准参数必须为有限数值")
        self._enabled = is_enabled
        self._version = model_version
        self._slope = calibration_slope
        self._intercept = calibration_intercept
        self._checkpoint = checkpoint_path
        self._device = device
        self._loaded_device: str | None = None
        self._context_length = context_length
        self._min_context = min_context
        self._evaluator = evaluator
        self._last_error: str | None = None
        self._error_code: str | None = None
        self._lock = Lock()

    def _load(self) -> Any:
        """仅在有效行情首次触发推理时加载真实模型。"""
        if self._evaluator is None:
            device = self._device
            if device in {"auto", "mps"}:
                torch = importlib.import_module("torch")
                mps_available = torch.backends.mps.is_available()
                if device == "mps" and not mps_available:
                    raise RuntimeError("当前 PyTorch 环境不支持 MPS，请检查设备或指定 cpu")
                device = "mps" if mps_available else "cpu"
            timesfm3 = importlib.import_module("timesfm3")
            config = timesfm3.ModelConfig(
                checkpoint_path=self._checkpoint,
                per_core_batch_size=1,
                device=device,
            )
            self._evaluator = timesfm3.TimesFM3Evaluator(config)
            self._loaded_device = str(self._evaluator.device)
        return self._evaluator

    def _hourly_prices(self, quotes: list[QuoteRaw], as_of: datetime) -> list[float]:
        """逐小时因果采样，遇到超过一小时的缺口即截断上下文。"""
        if as_of.tzinfo is None:
            raise ValueError("预测时点必须包含时区")
        if len({(quote.source_id, quote.product_code) for quote in quotes}) > 1:
            raise ValueError("单次预测不得混合数据源或产品")
        eligible = sorted(
            (
                quote
                for quote in quotes
                if quote.is_tradable
                and quote.quality_state == DataQualityState.NORMAL
                and max(quote.quote_at, quote.received_at, quote.available_at or quote.received_at) <= as_of
            ),
            key=lambda quote: quote.quote_at,
        )
        if any(not math.isfinite(quote.bid_price) or quote.bid_price <= 0 for quote in eligible):
            raise ValueError("预测报价必须为有限正数")
        end = as_of.astimezone(UTC)
        prices: list[float] = []
        for offset in range(self._context_length):
            sample_at = end - timedelta(hours=offset)
            latest = next(
                (
                    quote
                    for quote in reversed(eligible)
                    if max(quote.quote_at, quote.received_at, quote.available_at or quote.received_at) <= sample_at
                ),
                None,
            )
            if latest is None or sample_at - latest.quote_at > timedelta(hours=1):
                break
            prices.append(latest.bid_price)
        return list(reversed(prices))

    def is_available(self) -> bool:
        """仅已加载且最近推理未失败的启用模型可用。"""
        return self._enabled and self._evaluator is not None and self._last_error is None

    def predict_feature(
        self,
        quotes: list[QuoteRaw],
        horizon: HoldingPeriod = HoldingPeriod.W1,
        as_of: datetime | None = None,
    ) -> float | None:
        """返回真实模型的终点收益预测，失败或样本不足时安全降级。"""
        if not self._enabled:
            return None
        with self._lock:
            try:
                effective_at = as_of or datetime.now(UTC)
                if effective_at.tzinfo is None:
                    raise ValueError("预测时点必须包含时区")
                effective_at = effective_at.astimezone(UTC)
                prices = self._hourly_prices(quotes, effective_at)
                if len(prices) < self._min_context:
                    self._last_error = "连续小时报价不足"
                    self._error_code = "insufficient_context"
                    return None
                steps = self._HORIZON_HOURS[horizon]
                evaluator = self._load()
                outputs = list(
                    evaluator.predict_batch(
                        [np.asarray(prices, dtype=np.float32)],
                        horizon=steps,
                        return_quantiles=True,
                        use_symmetric_averaging=False,
                    )
                )
                if len(outputs) != 1 or outputs[0].forecast is None:
                    raise ValueError("模型未返回单序列价格预测")
                forecast = np.asarray(outputs[0].forecast, dtype=float)
                if forecast.shape != (steps,) or not np.all(np.isfinite(forecast)) or np.any(forecast <= 0):
                    raise ValueError("模型价格预测的形状或数值无效")
                target = float(forecast[-1])
                result = self._slope * (target / prices[-1] - 1.0) + self._intercept
                if not math.isfinite(result):
                    raise ValueError("模型收益预测不是有限值")
                self._last_error = None
                self._error_code = None
                return round(result, 6)
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._error_code = "inference_failed"
                logging.getLogger(__name__).warning("TimesFM 推理失败：%s", self._last_error)
                return None

    def get_model_info(self) -> dict[str, str | bool | float | None]:
        """返回实际模型来源、加载状态和最近失败原因。"""
        return {
            "model_version": self._version,
            "checkpoint_path": self._checkpoint,
            "requested_device": self._device,
            "device": self._loaded_device,
            "is_enabled": self._enabled,
            "model_loaded": self._evaluator is not None,
            "is_available": self.is_available(),
            "calibration_slope": self._slope,
            "calibration_intercept": self._intercept,
            "last_error": self._last_error,
            "error_code": self._error_code,
        }
