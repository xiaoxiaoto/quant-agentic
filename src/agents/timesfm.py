"""TimesFM 3.0 预测 Agent，使用真实权重的懒加载推理。

使用小时等频历史报价预测价格，再转换为收益特征；失败时返回缺失值。
同时提供带协变量的多因子批量条件预测与按决策时点缓存的收益分布特征。
下载权重的使用范围受其独立许可证约束，启用不代表已获生产授权。
"""

from __future__ import annotations

import importlib
import logging
import math
import statistics
import sys
from collections import OrderedDict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, ClassVar

import numpy as np

from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.timesfm_multifactor import TimesFMFinetuneSummary, TimesFMForecastDistribution
from src.contracts.enums.trading import DataQualityState, HoldingPeriod
from src.contracts.exceptions import TimesFMTrainingError


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
    _DIST_CACHE_SIZE: ClassVar[int] = 64

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
        self._dist_cache: OrderedDict[tuple[str, str], TimesFMForecastDistribution] = OrderedDict()
        self._inference_lock = Lock()
        self._state_lock = Lock()

    def _resolve_device(self) -> str:
        """解析请求设备；显式 CUDA/MPS 不可用时报错，auto 按 CUDA、MPS、CPU 顺序回退。"""
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
        return device

    def _build_evaluator(self, checkpoint: str, device: str) -> Any:
        """按给定权重路径与设备构造官方 TimesFM3 评估器。"""
        timesfm3 = importlib.import_module("timesfm3")
        config = timesfm3.ModelConfig(
            checkpoint_path=checkpoint,
            per_core_batch_size=1,
            device=device,
        )
        return timesfm3.TimesFM3Evaluator(config)

    def _load(self) -> Any:
        """仅在有效行情首次触发推理时加载真实模型。"""
        if self._evaluator is None:
            device = self._resolve_device()
            evaluator = self._build_evaluator(self._checkpoint, device)
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

    @staticmethod
    def _build_distribution(
        base_price: float,
        forecast: np.ndarray,
        quantiles: Any,
    ) -> TimesFMForecastDistribution:
        """由价格预测路径与分位数矩阵构造收益分布特征；缺少分位数时退化为零宽度分布。"""
        point = float(forecast[-1]) / base_price - 1.0
        path_mean = float(np.mean(forecast)) / base_price - 1.0
        path_min = float(np.min(forecast)) / base_price - 1.0
        q10 = q90 = point
        iqr = 0.0
        skew = 0.0
        if quantiles is not None:
            matrix = np.asarray(quantiles, dtype=float)
            if matrix.ndim == 2 and matrix.shape[0] == forecast.shape[0] and matrix.shape[1] >= 3:
                last = np.sort(matrix[-1])
                if np.all(np.isfinite(last)):
                    count = last.shape[0]
                    q10 = float(last[0]) / base_price - 1.0
                    q90 = float(last[-1]) / base_price - 1.0
                    low_mid = float(last[round(0.25 * (count - 1))]) / base_price - 1.0
                    high_mid = float(last[round(0.75 * (count - 1))]) / base_price - 1.0
                    iqr = high_mid - low_mid
                    spread = q90 - q10
                    skew = ((q90 - point) - (point - q10)) / spread if spread > 1e-12 else 0.0
        values = (point, q10, q90, iqr, path_mean, path_min, skew)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("模型收益分布特征不是有限值")
        return TimesFMForecastDistribution(
            point_return=point,
            q10_return=q10,
            q90_return=q90,
            iqr_return=iqr,
            path_mean_return=path_mean,
            path_min_return=path_min,
            skew=max(-1.0, min(1.0, skew)),
        )

    def _cache_distribution(self, as_of: datetime, horizon: HoldingPeriod, dist: TimesFMForecastDistribution) -> None:
        key = (as_of.astimezone(UTC).isoformat(), horizon.value)
        with self._state_lock:
            self._dist_cache[key] = dist
            self._dist_cache.move_to_end(key)
            while len(self._dist_cache) > self._DIST_CACHE_SIZE:
                self._dist_cache.popitem(last=False)

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
                try:
                    dist = self._build_distribution(prices[-1], forecast, getattr(outputs[0], "quantiles", None))
                except ValueError:
                    dist = None
                if dist is not None:
                    self._cache_distribution(effective_at, horizon, dist)
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

    def predict_multifactor_batch(
        self,
        contexts: Sequence[np.ndarray],
        horizon_steps: int,
        *,
        past_only_covariates: Sequence[np.ndarray] | None = None,
        past_future_covariates: Sequence[np.ndarray] | None = None,
        batch_size: int = 16,
    ) -> list[TimesFMForecastDistribution]:
        """批量执行带协变量的条件预测，返回各序列的收益分布特征（不使用线性校准）。

        contexts[i] 形状为 (上下文长度,)；past_only_covariates[i] 为 (通道数, 上下文长度)；
        past_future_covariates[i] 为 (通道数, 上下文长度 + horizon_steps)。
        """
        if horizon_steps < 1:
            raise ValueError("预测步数必须为正整数")
        if batch_size < 1:
            raise ValueError("批量大小必须为正整数")
        count = len(contexts)
        for covariates in (past_only_covariates, past_future_covariates):
            if covariates is not None and len(covariates) != count:
                raise ValueError("协变量数量必须与上下文数量一致")
        if count == 0:
            return []
        validated_contexts: list[np.ndarray] = []
        for item in contexts:
            series = np.asarray(item, dtype=np.float32)
            if series.ndim != 1 or series.size == 0 or not np.all(np.isfinite(series)):
                raise ValueError("输入上下文序列必须为一维非空且全为有限浮点数")
            base_price = float(series[-1])
            if not math.isfinite(base_price) or base_price <= 0:
                raise ValueError("模型价格预测或基准价必须为正数")
            validated_contexts.append(series)
        results: list[TimesFMForecastDistribution] = []
        with self._inference_lock:
            evaluator = self._load()
            for start in range(0, count, batch_size):
                end = min(start + batch_size, count)
                kwargs: dict[str, Any] = {
                    "horizon": horizon_steps,
                    "return_quantiles": True,
                    "use_symmetric_averaging": False,
                }
                if past_only_covariates is not None:
                    kwargs["past_only_covariates"] = [
                        np.asarray(item, dtype=np.float32) for item in past_only_covariates[start:end]
                    ]
                if past_future_covariates is not None:
                    kwargs["past_future_covariates"] = [
                        np.asarray(item, dtype=np.float32) for item in past_future_covariates[start:end]
                    ]
                batch = validated_contexts[start:end]
                outputs = list(evaluator.predict_batch(batch, **kwargs))
                if len(outputs) != len(batch):
                    raise ValueError("模型返回的序列数量与输入不一致")
                for series, output in zip(batch, outputs, strict=True):
                    if output.forecast is None:
                        raise ValueError("模型未返回价格预测")
                    forecast = np.asarray(output.forecast, dtype=float)
                    if forecast.shape != (horizon_steps,) or not np.all(np.isfinite(forecast)):
                        raise ValueError("模型价格预测的形状或数值无效")
                    if np.any(forecast <= 0):
                        raise ValueError("模型价格预测或基准价必须为正数")
                    results.append(
                        self._build_distribution(float(series[-1]), forecast, getattr(output, "quantiles", None))
                    )
        return results

    def get_cached_distribution(self, as_of: datetime, horizon: HoldingPeriod) -> TimesFMForecastDistribution | None:
        """返回与决策时点对齐的最近一次分布特征，未命中返回 None。"""
        if as_of.tzinfo is None:
            return None
        key = (as_of.astimezone(UTC).isoformat(), horizon.value)
        with self._state_lock:
            return self._dist_cache.get(key)

    def clone_isolated(self, *, checkpoint_path: str | None = None) -> TimesFMAgent:
        """创建与当前实例完全隔离的新 TimesFMAgent，供离线训练与回测独立加载或微调权重。"""
        with self._state_lock:
            checkpoint = self._checkpoint if checkpoint_path is None else checkpoint_path
            return TimesFMAgent(
                is_enabled=self._enabled,
                model_version=self._version,
                calibration_slope=self._slope,
                calibration_intercept=self._intercept,
                checkpoint_path=checkpoint,
                device=self._device,
                context_length=self._context_length,
                min_context=self._min_context,
            )

    def finetune_backbone(
        self,
        *,
        contexts: Sequence[np.ndarray],
        targets: Sequence[np.ndarray],
        steps: int,
        checkpoint_dir: Path,
        past_only_covariates: Sequence[np.ndarray] | None = None,
        past_future_covariates: Sequence[np.ndarray] | None = None,
        epochs: int = 1,
        learning_rate: float = 1e-4,
        batch_size: int = 4,
        unfreeze_last_layers: int = 0,
    ) -> TimesFMFinetuneSummary:
        """在推理互斥锁保护下以分位数损失微调输出头（可选末尾若干层）并保存权重。"""
        if not contexts or len(contexts) != len(targets):
            raise TimesFMTrainingError("微调样本窗口与目标序列数量无效")
        if steps <= 0 or epochs <= 0 or batch_size <= 0 or unfreeze_last_layers < 0:
            raise TimesFMTrainingError("微调步长、轮数、批大小或解冻层数参数无效")
        if not math.isfinite(learning_rate) or learning_rate <= 0.0:
            raise TimesFMTrainingError("微调学习率必须为有限正数")
        if past_only_covariates is not None and len(past_only_covariates) != len(contexts):
            raise TimesFMTrainingError("仅历史协变量数量与样本窗口不一致")
        if past_future_covariates is not None and len(past_future_covariates) != len(contexts):
            raise TimesFMTrainingError("历史+未来协变量数量与样本窗口不一致")
        for idx, (ctx_raw, tgt_raw) in enumerate(zip(contexts, targets, strict=True)):
            ctx_arr = np.asarray(ctx_raw, dtype=np.float32)
            tgt_arr = np.asarray(tgt_raw, dtype=np.float32)
            if ctx_arr.ndim != 1 or ctx_arr.size == 0 or not bool(np.all(np.isfinite(ctx_arr))):
                raise TimesFMTrainingError(f"第 {idx} 条微调上下文窗口无效或包含非有限值")
            if float(ctx_arr[-1]) <= 0.0:
                raise TimesFMTrainingError(f"第 {idx} 条微调上下文基准价必须为正数")
            if tgt_arr.ndim != 1 or tgt_arr.size != steps or not bool(np.all(np.isfinite(tgt_arr))):
                raise TimesFMTrainingError(f"第 {idx} 条微调目标序列长度不匹配或包含非有限值")
            if past_only_covariates is not None:
                po_arr = np.asarray(past_only_covariates[idx], dtype=np.float32)
                if not bool(np.all(np.isfinite(po_arr))):
                    raise TimesFMTrainingError(f"第 {idx} 条仅历史协变量包含非有限值")
            if past_future_covariates is not None:
                pf_arr = np.asarray(past_future_covariates[idx], dtype=np.float32)
                if not bool(np.all(np.isfinite(pf_arr))):
                    raise TimesFMTrainingError(f"第 {idx} 条历史+未来协变量包含非有限值")
        torch = importlib.import_module("torch")
        if sys.platform == "darwin" and torch.get_num_threads() > 1:
            torch.set_num_threads(1)
        with self._inference_lock:
            model = getattr(self._load(), "model", None)
            decode_fn = getattr(getattr(model, "decode", None), "__wrapped__", None)
            if model is None or decode_fn is None:
                raise TimesFMTrainingError("当前 TimesFM 评估器不支持主干微调")
            for param in model.parameters():
                param.requires_grad_(False)
            model.output_head.requires_grad_(True)
            if unfreeze_last_layers > 0:
                for layer in list(model.transformer_stack.layers)[-unfreeze_last_layers:]:
                    layer.requires_grad_(True)
            trainable = [p for p in model.parameters() if p.requires_grad]
            total_params = sum(int(p.numel()) for p in model.parameters())
            optimizer = torch.optim.AdamW(trainable, lr=learning_rate)
            device = next(model.parameters()).device
            quantiles = torch.tensor(list(model.quantiles), dtype=torch.float32, device=device).view(1, 1, -1)
            rng = np.random.default_rng(42)
            previous_revin = model.use_iterative_cpm_revin
            model.use_iterative_cpm_revin = False
            model.train()
            initial_loss: float | None = None
            epoch_loss = 0.0
            sample_count = len(contexts)
            try:
                for _ in range(epochs):
                    order = rng.permutation(sample_count)
                    losses: list[float] = []
                    for start in range(0, len(order), batch_size):
                        chunk = order[start : start + batch_size]
                        ctx_b = [np.asarray(contexts[int(i)], dtype=np.float32) for i in chunk]
                        target_b = [np.asarray(targets[int(i)], dtype=np.float32) for i in chunk]
                        po_b = (
                            [np.asarray(past_only_covariates[int(i)], dtype=np.float32) for i in chunk]
                            if past_only_covariates is not None
                            else None
                        )
                        pf_b = (
                            [np.asarray(past_future_covariates[int(i)], dtype=np.float32) for i in chunk]
                            if past_future_covariates is not None
                            else None
                        )
                        ctx_t = torch.tensor(np.stack(ctx_b), dtype=torch.float32, device=device).unsqueeze(1)
                        po_t = torch.tensor(np.stack(po_b), dtype=torch.float32, device=device) if po_b else None
                        pf_t = torch.tensor(np.stack(pf_b), dtype=torch.float32, device=device) if pf_b else None
                        target_t = torch.tensor(np.stack(target_b), dtype=torch.float32, device=device)
                        logits = decode_fn(
                            model,
                            target=ctx_t,
                            horizon=steps,
                            past_only_covariates=po_t,
                            past_future_covariates=pf_t,
                        )
                        base = ctx_t[:, 0, -1]
                        pred_ret = (logits[:, 0] / base[:, None, None] - 1.0) * 100.0
                        true_ret = (target_t / base[:, None] - 1.0) * 100.0
                        diff = true_ret.unsqueeze(-1) - pred_ret
                        loss = torch.maximum(quantiles * diff, (quantiles - 1.0) * diff).mean()
                        if not bool(torch.isfinite(loss)):
                            raise TimesFMTrainingError("TimesFM 微调损失出现非有限值，已中止并拒绝保存损坏权重")
                        optimizer.zero_grad()
                        loss.backward()
                        optimizer.step()
                        value = float(loss.detach().cpu())
                        if initial_loss is None:
                            initial_loss = value
                        losses.append(value)
                    epoch_loss = statistics.mean(losses)
            finally:
                for param in model.parameters():
                    param.requires_grad_(False)
                model.use_iterative_cpm_revin = previous_revin
                model.eval()
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(str(checkpoint_dir))
            with self._state_lock:
                self._dist_cache.clear()
        return TimesFMFinetuneSummary(
            enabled=True,
            epochs=epochs,
            learning_rate=learning_rate,
            unfreeze_last_layers=unfreeze_last_layers,
            trainable_params=sum(int(p.numel()) for p in trainable),
            total_params=total_params,
            sample_count=sample_count,
            initial_loss=initial_loss,
            final_loss=epoch_loss,
            checkpoint_saved=True,
        )

    def reload_checkpoint(
        self,
        checkpoint_path: str,
        *,
        calibration_slope: float | None = None,
        calibration_intercept: float | None = None,
    ) -> None:
        """切换权重路径并可选更新线性校准参数；成功后清空分布缓存与最近错误。"""
        slope = self._slope if calibration_slope is None else calibration_slope
        intercept = self._intercept if calibration_intercept is None else calibration_intercept
        if not all(math.isfinite(value) for value in (slope, intercept)):
            raise ValueError("校准参数必须为有限数值")
        with self._inference_lock:
            device = self._loaded_device or self._resolve_device()
            evaluator = self._build_evaluator(checkpoint_path, device)
            loaded_device = str(evaluator.device)
            with self._state_lock:
                self._evaluator = evaluator
                self._loaded_device = loaded_device
                self._checkpoint = checkpoint_path
                self._slope = slope
                self._intercept = intercept
                self._dist_cache.clear()
                self._last_error = None
                self._error_code = None

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

    @property
    def context_length(self) -> int:
        """返回当前 TimesFM Agent 实际生效的上下文长度。"""
        return self._context_length
