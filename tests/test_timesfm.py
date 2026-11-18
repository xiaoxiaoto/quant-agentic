"""真实时序模型适配器的输入与推理契约测试。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from src.agents.timesfm import TimesFMAgent
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import HoldingPeriod


class RecordingEvaluator:
    def __init__(self, output: float = 660.0, error: bool = False) -> None:
        self.output = output
        self.error = error
        self.contexts: Any = None
        self.horizon = 0
        self.device = "cpu"

    def predict_batch(self, contexts: Any, horizon: int, **kwargs: Any) -> list[Any]:
        self.contexts = contexts
        self.horizon = horizon
        assert kwargs == {"return_quantiles": True, "use_symmetric_averaging": False}
        if self.error:
            raise RuntimeError("推理失败")
        return [SimpleNamespace(forecast=np.full(horizon, self.output))]


def hourly_quotes(count: int = 40) -> list[QuoteRaw]:
    start = datetime(2026, 3, 2, 0, tzinfo=UTC)
    return [
        QuoteRaw(
            quote_id=f"q_{i}",
            source_id="src",
            bid_price=600.0,
            ask_price=600.5,
            quote_at=start + timedelta(hours=i),
            received_at=start + timedelta(hours=i),
            is_tradable=True,
        )
        for i in range(count)
    ]


def test_timesfm_adapter_disabled_fallback() -> None:
    adapter = TimesFMAgent()
    assert not adapter.is_available()
    assert adapter.predict_feature(hourly_quotes()) is None
    assert adapter.get_model_info()["model_loaded"] is False
    assert adapter.get_model_info()["error_code"] is None


def test_timesfm_adapter_prediction_output() -> None:
    evaluator = RecordingEvaluator()
    adapter = TimesFMAgent(is_enabled=True, evaluator=evaluator)
    quotes = hourly_quotes()
    result = adapter.predict_feature(quotes, HoldingPeriod.W1, as_of=quotes[-1].quote_at)
    assert result == pytest.approx(0.1)
    assert evaluator.horizon == 168
    np.testing.assert_array_equal(evaluator.contexts[0], [600.0] * 40)
    assert adapter.is_available()
    assert adapter.get_model_info()["error_code"] is None


def test_enabled_model_is_lazy_and_load_failure_is_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = TimesFMAgent(is_enabled=True)
    assert not adapter.is_available()

    def fail_import(name: str) -> None:
        raise ImportError("缺少模型依赖")

    monkeypatch.setattr("src.agents.timesfm.importlib.import_module", fail_import)
    quotes = hourly_quotes()
    assert adapter.predict_feature(quotes, as_of=quotes[-1].quote_at) is None
    assert "ImportError" in str(adapter.get_model_info()["last_error"])
    assert adapter.get_model_info()["error_code"] == "inference_failed"
    assert not adapter.is_available()


def test_dense_ticks_do_not_count_as_hourly_context() -> None:
    quotes = hourly_quotes()
    for i, quote in enumerate(quotes):
        quote.quote_at = quotes[0].quote_at + timedelta(minutes=i)
        quote.received_at = quote.quote_at
    evaluator = RecordingEvaluator()
    adapter = TimesFMAgent(is_enabled=True, evaluator=evaluator)
    assert adapter.predict_feature(quotes, as_of=quotes[-1].quote_at) is None
    assert evaluator.contexts is None
    assert adapter.get_model_info()["error_code"] == "insufficient_context"


def test_long_gap_truncates_context_instead_of_filling_weekend() -> None:
    quotes = hourly_quotes()
    for quote in quotes[-10:]:
        quote.quote_at += timedelta(days=2)
        quote.received_at = quote.quote_at
    evaluator = RecordingEvaluator()
    adapter = TimesFMAgent(is_enabled=True, evaluator=evaluator)
    assert adapter.predict_feature(quotes, as_of=quotes[-1].quote_at) is None
    assert evaluator.contexts is None


def test_future_and_late_quotes_are_not_visible() -> None:
    quotes = hourly_quotes()
    as_of = quotes[-1].quote_at
    future = quotes[-1].model_copy(update={"quote_at": as_of + timedelta(hours=1), "bid_price": 900.0})
    delayed = quotes[-1].model_copy(update={"received_at": as_of + timedelta(hours=1), "bid_price": 900.0})
    unavailable = quotes[-1].model_copy(update={"available_at": as_of + timedelta(hours=1), "bid_price": 900.0})
    evaluator = RecordingEvaluator()
    adapter = TimesFMAgent(is_enabled=True, evaluator=evaluator)
    assert adapter.predict_feature(quotes + [future, delayed, unavailable], as_of=as_of) == pytest.approx(0.1)
    np.testing.assert_array_equal(evaluator.contexts[0], [600.0] * 40)


def test_mixed_sources_or_products_are_rejected() -> None:
    quotes = hourly_quotes()
    quotes[-1].source_id = "other"
    adapter = TimesFMAgent(is_enabled=True, evaluator=RecordingEvaluator())
    assert adapter.predict_feature(quotes, as_of=quotes[-1].quote_at) is None
    assert "不得混合" in str(adapter.get_model_info()["last_error"])


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -1.0, 0.0])
def test_bad_input_price_is_rejected(price: float) -> None:
    quotes = hourly_quotes()
    quotes[-1].bid_price = price
    evaluator = RecordingEvaluator()
    adapter = TimesFMAgent(is_enabled=True, evaluator=evaluator)
    assert adapter.predict_feature(quotes, as_of=quotes[-1].quote_at) is None
    assert evaluator.contexts is None


@pytest.mark.parametrize("output", [float("nan"), float("inf"), -1.0, 0.0])
def test_invalid_model_output_is_not_a_feature(output: float) -> None:
    quotes = hourly_quotes()
    adapter = TimesFMAgent(is_enabled=True, evaluator=RecordingEvaluator(output=output))
    assert adapter.predict_feature(quotes, as_of=quotes[-1].quote_at) is None
    assert not adapter.is_available()


def test_inference_failure_does_not_fall_back_to_momentum() -> None:
    quotes = hourly_quotes()
    adapter = TimesFMAgent(is_enabled=True, evaluator=RecordingEvaluator(error=True))
    assert adapter.predict_feature(quotes, as_of=quotes[-1].quote_at) is None
    assert "推理失败" in str(adapter.get_model_info()["last_error"])
    assert adapter.get_model_info()["error_code"] == "inference_failed"


def test_context_error_clears_after_successful_warmup() -> None:
    quotes = hourly_quotes()
    adapter = TimesFMAgent(is_enabled=True, evaluator=RecordingEvaluator())
    assert adapter.predict_feature(quotes[:4], as_of=quotes[3].quote_at) is None
    assert adapter.get_model_info()["error_code"] == "insufficient_context"
    assert adapter.predict_feature(quotes, as_of=quotes[-1].quote_at) == pytest.approx(0.1)
    assert adapter.get_model_info()["error_code"] is None


def test_non_hour_decision_anchors_hourly_context_to_decision_time() -> None:
    class LinearEvaluator(RecordingEvaluator):
        def predict_batch(self, contexts: Any, horizon: int, **kwargs: Any) -> list[Any]:
            self.horizon = horizon
            return [SimpleNamespace(forecast=np.array([660.0]))]

    evaluator = LinearEvaluator()
    quotes = hourly_quotes()
    adapter = TimesFMAgent(is_enabled=True, evaluator=evaluator)
    as_of = quotes[-1].quote_at + timedelta(minutes=30)
    assert adapter.predict_feature(quotes, HoldingPeriod.H1, as_of=as_of) == pytest.approx(0.1)
    assert evaluator.horizon == 1


def test_hourly_feed_with_receive_latency_is_not_rejected() -> None:
    quotes = hourly_quotes()
    for quote in quotes:
        quote.received_at += timedelta(seconds=1)
        quote.available_at = quote.received_at
    evaluator = RecordingEvaluator()
    adapter = TimesFMAgent(is_enabled=True, evaluator=evaluator)
    assert adapter.predict_feature(quotes, HoldingPeriod.H1, as_of=quotes[-1].received_at) == pytest.approx(0.1)
    np.testing.assert_array_equal(evaluator.contexts[0], [600.0] * 40)


def test_quote_exactly_one_hour_old_is_valid_but_older_is_missing() -> None:
    quotes = hourly_quotes()
    evaluator = RecordingEvaluator()
    adapter = TimesFMAgent(is_enabled=True, evaluator=evaluator)
    assert adapter.predict_feature(quotes, as_of=quotes[-1].quote_at + timedelta(hours=1)) == pytest.approx(0.1)
    assert adapter.predict_feature(quotes, as_of=quotes[-1].quote_at + timedelta(hours=1, seconds=1)) is None


@pytest.mark.parametrize(
    ("requested_device", "mps_available", "device"),
    [("cpu", True, "cpu"), ("auto", True, "mps"), ("auto", False, "cpu"), ("mps", True, "mps")],
)
def test_loader_uses_official_checkpoint_and_loads_once(
    monkeypatch: pytest.MonkeyPatch, requested_device: str, mps_available: bool, device: str
) -> None:
    evaluator = RecordingEvaluator()
    configs: list[dict[str, Any]] = []
    loads: list[str] = []

    def config_factory(**kwargs: Any) -> dict[str, Any]:
        configs.append(kwargs)
        return kwargs

    def evaluator_factory(config: dict[str, Any]) -> RecordingEvaluator:
        loads.append(config["checkpoint_path"])
        evaluator.device = config["device"]
        return evaluator

    module = SimpleNamespace(ModelConfig=config_factory, TimesFM3Evaluator=evaluator_factory)
    torch = SimpleNamespace(backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps_available)))
    monkeypatch.setattr(
        "src.agents.timesfm.importlib.import_module",
        {"torch": torch, "timesfm3": module}.__getitem__,
    )
    adapter = TimesFMAgent(is_enabled=True, checkpoint_path="/models/timesfm3", device=requested_device)
    quotes = hourly_quotes()
    assert adapter.predict_feature(quotes, HoldingPeriod.D1, as_of=quotes[-1].quote_at) == pytest.approx(0.1)
    assert adapter.predict_feature(quotes, HoldingPeriod.D1, as_of=quotes[-1].quote_at) == pytest.approx(0.1)
    assert configs == [{"checkpoint_path": "/models/timesfm3", "per_core_batch_size": 1, "device": device}]
    assert adapter.get_model_info()["device"] == device
    assert loads == ["/models/timesfm3"]
    assert evaluator.horizon == 24


def test_explicit_mps_fails_without_cpu_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    torch = SimpleNamespace(backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)))
    monkeypatch.setattr("src.agents.timesfm.importlib.import_module", {"torch": torch}.__getitem__)
    quotes = hourly_quotes()
    adapter = TimesFMAgent(is_enabled=True, device="mps")
    assert adapter.predict_feature(quotes, as_of=quotes[-1].quote_at) is None
    assert "不支持 MPS" in str(adapter.get_model_info()["last_error"])


def test_forecast_shape_is_validated() -> None:
    class WrongShapeEvaluator(RecordingEvaluator):
        def predict_batch(self, contexts: Any, horizon: int, **kwargs: Any) -> list[Any]:
            return [SimpleNamespace(forecast=np.array([[660.0] * horizon]))]

    quotes = hourly_quotes()
    adapter = TimesFMAgent(is_enabled=True, evaluator=WrongShapeEvaluator())
    assert adapter.predict_feature(quotes, as_of=quotes[-1].quote_at) is None
    assert "形状" in str(adapter.get_model_info()["last_error"])
