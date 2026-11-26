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
        self._inference_lock = Lock()
        self._state_lock = Lock()

    def _load(self) -> Any:
        """仅在有效行情首次触发推理时加载真实模型。"""
        if self._evaluator is None:
            device = self._device
            if device in {"auto", "mps", "cuda"}:
                torch = importlib.import_module("torch")
                cuda_mod = getattr(torch, "cuda", None)
                cuda_available = bool(cuda_mod is not None and cuda_mod.is_available())
                backends_mod = getattr(torch, "backends", None)
                mps_mod = getattr(backends_mod, "mps", None)
                mps_available = bool(mps_mod is not None and mps_mod.is_available())
                if device == "cuda" and not cuda_available:
                    raise RuntimeError("当前 PyTorch 环境不支持 CUDA，请检查设备或指定 cpu")
                if device == "mps" and not mps_available:
                    raise RuntimeError("当前 PyTorch 环境不支持 MPS，请检查设备或指定 cpu")
                if device == "auto":
                    device = "cuda" if cuda_available else ("mps" if mps_available else "cpu")
            timesfm3 = importlib.import_module("timesfm3")
            config = timesfm3.ModelConfig(
                checkpoint_path=self._checkpoint,
                per_core_batch_size=1,
                device=device,
            )
            evaluator = timesfm3.TimesFM3Evaluator(config)
            loaded_device = str(evaluator.device)
            with self._state_lock:
                self._evaluator = evaluator
                self._loaded_device = loaded_device
        return self._evaluator

    def _hourly_prices(self, quotes: list[QuoteRaw], as_of: datetime) -> list[float]:
        """逐小时因果采样，遇到超过一小时的缺口即截断上下文（O(N log N + context_length)）。"""
        if as_of.tzinfo is None:
            raise ValueError("预测时点必须包含时区")
        if len({(quote.source_id, quote.product_code) for quote in quotes}) > 1:
            raise ValueError("单次预测不得混合数据源或产品")
        eligible_pairs: list[tuple[datetime, QuoteRaw]] = []
        for quote in quotes:
            if quote.is_tradable and quote.quality_state == DataQualityState.NORMAL:
                vis = max(quote.quote_at, quote.received_at, quote.available_at or quote.received_at)
                if vis <= as_of:
                    eligible_pairs.append((vis, quote))
        if any(not math.isfinite(quote.bid_price) or quote.bid_price <= 0 for _, quote in eligible_pairs):
            raise ValueError("预测报价必须为有限正数")
        if not eligible_pairs:
            return []
        eligible_pairs.sort(key=lambda item: (item[0], item[1].quote_at))
        prefix_best: list[QuoteRaw] = []
        best_so_far: QuoteRaw | None = None
        for _, quote in eligible_pairs:
            if best_so_far is None or quote.quote_at >= best_so_far.quote_at:
                best_so_far = quote
            prefix_best.append(best_so_far)

        end = as_of.astimezone(UTC)
        prices: list[float] = []
        ptr = len(eligible_pairs) - 1
        for offset in range(self._context_length):
            sample_at = end - timedelta(hours=offset)
            while ptr >= 0 and eligible_pairs[ptr][0] > sample_at:
                ptr -= 1
            if ptr < 0:
                break
            latest = prefix_best[ptr]
            if sample_at - latest.quote_at > timedelta(hours=1):
                break
            prices.append(latest.bid_price)
        return list(reversed(prices))

    def is_available(self) -> bool:
        """仅已加载且最近推理未失败的启用模型可用。"""
        with self._state_lock:
            return self._enabled and self._evaluator is not None and self._last_error is None

    def predict_feature_with_status(
        self,
        quotes: list[QuoteRaw],
        horizon: HoldingPeriod = HoldingPeriod.W1,
        as_of: datetime | None = None,
    ) -> tuple[float | None, str | None]:
        """返回 (终点收益预测, 错误码) 元组，避免并发调用方通过共享属性竞态读取错误状态。"""
        if not self._enabled:
            return None, None
        with self._inference_lock:
            try:
                effective_at = as_of or datetime.now(UTC)
                if effective_at.tzinfo is None:
                    raise ValueError("预测时点必须包含时区")
                effective_at = effective_at.astimezone(UTC)
                prices = self._hourly_prices(quotes, effective_at)
                if len(prices) < self._min_context:
                    with self._state_lock:
                        self._last_error = "连续小时报价不足"
                        self._error_code = "insufficient_context"
                    return None, "insufficient_context"
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
                with self._state_lock:
                    self._last_error = None
                    self._error_code = None
                return round(result, 6), None
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                with self._state_lock:
                    self._last_error = err_msg
                    self._error_code = "inference_failed"
                logging.getLogger(__name__).warning("TimesFM 推理失败：%s", err_msg)
                return None, "inference_failed"

    def predict_feature(
        self,
        quotes: list[QuoteRaw],
        horizon: HoldingPeriod = HoldingPeriod.W1,
        as_of: datetime | None = None,
    ) -> float | None:
        """返回真实模型的终点收益预测，失败或样本不足时安全降级。"""
        value, _ = self.predict_feature_with_status(quotes, horizon=horizon, as_of=as_of)
        return value

    def get_model_info(self) -> dict[str, str | bool | float | None]:
        """返回实际模型来源、加载状态和最近失败原因。"""
        with self._state_lock:
            available = self._enabled and self._evaluator is not None and self._last_error is None
            return {
                "model_version": self._version,
                "checkpoint_path": self._checkpoint,
                "requested_device": self._device,
                "device": self._loaded_device,
                "is_enabled": self._enabled,
                "model_loaded": self._evaluator is not None,
                "is_available": available,
                "calibration_slope": self._slope,
                "calibration_intercept": self._intercept,
                "last_error": self._last_error,
                "error_code": self._error_code,
            }
