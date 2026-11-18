"""真实模型接线及新闻风险对决策链的集成验证。"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
from src.agents.lightgbm import LightGBMAgent, TrainingSample
from src.agents.news import NewsAgent
from src.agents.timesfm import TimesFMAgent
from src.configs.settings import Settings
from src.contracts.domains.feature import FeatureSet
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import HoldingPeriod
from src.runtime.dependencies import RuntimeDependencies
from src.services.execution.shadow_runner import ShadowRunner
from src.services.prediction.feature_engine import FeatureEngine


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[RuntimeDependencies]:
    instance = RuntimeDependencies(
        Settings.model_construct(data_dir=str(tmp_path), timesfm_enabled=False, news_llm_enabled=False)
    )
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: instance
    yield instance
    app.dependency_overrides.clear()


def test_status_identifies_research_fallback_and_disabled_news(runtime: RuntimeDependencies) -> None:
    with TestClient(app) as client:
        status = client.get("/api/v1/models/status").json()
        assert status["predictor"]["model_version"] == "heuristic_v1"
        assert status["timesfm"]["model_loaded"] is False
        assert status["news_llm"]["framework"] == "pydantic-ai"
        assert "api_key" not in json.dumps(status)
        response = client.post(
            "/api/v1/models/news/extract",
            json={
                "title": "新闻",
                "content": "正文",
                "source": "feed",
                "published_at": datetime.now(UTC).isoformat(),
            },
        )
        assert response.status_code == 503


def test_pydantic_ai_news_flows_into_shared_features_and_decision(runtime: RuntimeDependencies) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "news-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "event_type": "MONETARY",
                                    "severity": 0.9,
                                    "confidence": 0.95,
                                    "summary": "重大货币政策风险",
                                }
                            ),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    runtime.news_agent = NewsAgent(
        "https://news.example/v1",
        "news-model",
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    with TestClient(app) as client:
        result = client.post(
            "/api/v1/models/news/extract",
            json={
                "title": "利率调整",
                "content": "央行发布利率政策",
                "source": "feed",
                "published_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            },
        )
        assert result.status_code == 200
        event = result.json()["event"]
        assert len(result.json()["risk_flags"]) == 1
        assert event["summary"] == "重大货币政策风险"
        now = datetime.now(UTC)
        quote = QuoteRaw(
            quote_id="news-quote",
            source_id="feed",
            bid_price=600,
            ask_price=601,
            quote_at=now,
            received_at=now,
            is_tradable=True,
        )
        assert runtime.features.extract_features([quote], as_of=now).event_risk_flag_count == 1
        decision = client.post("/api/v1/decisions/evaluate", json={"quote": quote.model_dump(mode="json")})
        assert decision.status_code == 200
        assert "NEWS_RISK_ACTIVE" in decision.json()["raw_decision"]["reason_codes"]
        assert decision.json()["prediction"]["model_version"] == "heuristic_v1"
        future = client.post(
            "/api/v1/models/news/extract",
            json={
                "title": "新闻",
                "content": "正文",
                "source": "feed",
                "published_at": (now + timedelta(hours=1)).isoformat(),
            },
        )
        assert future.status_code == 422
        assert client.portal is not None
        assert runtime.news_agent is not None
        client.portal.call(runtime.news_agent.close)


def test_enabled_timesfm_missing_context_stops_decision(runtime: RuntimeDependencies) -> None:
    runtime.settings.timesfm_enabled = True
    with TestClient(app) as client:
        now = datetime.now(UTC).isoformat()
        response = client.post(
            "/api/v1/decisions/evaluate",
            json={
                "quote": {
                    "quote_id": "short",
                    "source_id": "feed",
                    "bid_price": 600,
                    "ask_price": 601,
                    "quote_at": now,
                    "received_at": now,
                    "is_tradable": True,
                }
            },
        )
        assert response.status_code == 503


def test_enabled_news_rejects_missing_model_configuration(tmp_path: Path) -> None:
    defaults = Settings.model_construct()
    assert defaults.timesfm_enabled
    assert defaults.news_llm_enabled
    with pytest.raises(ValueError, match="名称和密钥"):
        RuntimeDependencies(Settings.model_construct(data_dir=str(tmp_path), news_llm_enabled=True))


@pytest.mark.parametrize("field", ["quote_at", "received_at", "available_at"])
def test_decision_rejects_future_quote_before_feature_inference(runtime: RuntimeDependencies, field: str) -> None:
    now = datetime.now(UTC)
    quote = {
        "quote_id": "future",
        "source_id": "feed",
        "bid_price": 600,
        "ask_price": 601,
        "quote_at": now.isoformat(),
        "received_at": now.isoformat(),
        "is_tradable": True,
    }
    quote[field] = (now + timedelta(hours=1)).isoformat()
    with TestClient(app) as client:
        assert client.post("/api/v1/decisions/evaluate", json={"quote": quote}).status_code == 422


def test_shadow_records_context_warmup_without_model_predictions() -> None:
    now = datetime.now(UTC)
    quote = QuoteRaw(
        quote_id="short",
        source_id="feed",
        bid_price=600,
        ask_price=601,
        quote_at=now,
        received_at=now,
        is_tradable=True,
    )
    features = FeatureEngine(timesfm_agent=TimesFMAgent(is_enabled=True))
    report = ShadowRunner(feature_engine=features).run_shadow_simulation([quote])
    assert report.total_trades == 0
    assert "模型上下文预热弃权 1 次" in report.summary


def test_shadow_stops_on_real_inference_failure_after_warmup() -> None:
    class FailingEvaluator:
        def predict_batch(self, *args: object, **kwargs: object) -> list[object]:
            raise RuntimeError("模型推理失败")

    now = datetime.now(UTC)
    quotes = [
        QuoteRaw(
            quote_id=f"warm-{index}",
            source_id="feed",
            bid_price=600,
            ask_price=601,
            quote_at=now - timedelta(hours=31 - index),
            received_at=now - timedelta(hours=31 - index),
            is_tradable=True,
        )
        for index in range(32)
    ]
    adapter = TimesFMAgent(is_enabled=True, evaluator=FailingEvaluator())
    features = FeatureEngine(timesfm_agent=adapter)
    with pytest.raises(RuntimeError, match="TimesFM"):
        ShadowRunner(feature_engine=features).run_shadow_simulation(quotes)
    assert adapter.get_model_info()["error_code"] == "inference_failed"


def test_real_lightgbm_artifact_reaches_runtime_and_decision_api(tmp_path: Path) -> None:
    rows = []
    for index in range(100):
        at = datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=index)
        momentum = 0.03 if index % 2 else -0.03
        rows.append(
            TrainingSample(
                features=FeatureSet(quote_id=f"train-{index}", as_of=at, return_1d=momentum),
                horizon=HoldingPeriod.W1,
                net_return=momentum - 0.004,
                label_end_at=at + timedelta(days=7),
            )
        )
    model_dir = tmp_path / "model"
    LightGBMAgent.train(rows, model_dir, num_boost_round=5)
    configured = RuntimeDependencies(
        Settings.model_construct(
            data_dir=str(tmp_path / "quotes"),
            lightgbm_model_dir=str(model_dir),
            timesfm_enabled=False,
            news_llm_enabled=False,
        )
    )
    app.dependency_overrides[RuntimeDependencies.from_request] = lambda: configured
    try:
        with TestClient(app) as client:
            now = datetime.now(UTC).isoformat()
            result = client.post(
                "/api/v1/decisions/evaluate",
                json={
                    "quote": {
                        "quote_id": "real-model",
                        "source_id": "feed",
                        "bid_price": 600,
                        "ask_price": 601,
                        "quote_at": now,
                        "received_at": now,
                        "is_tradable": True,
                    }
                },
            )
            assert result.status_code == 200
            assert result.json()["prediction"]["model_version"] == "lightgbm_v1"
            assert result.json()["prediction"]["is_calibrated"] is True
            assert client.get("/api/v1/models/status").json()["predictor"]["production_approved"] is False
    finally:
        app.dependency_overrides.clear()
