"""TimesFM 多因子全链路端到端集成测试：准备 -> 训练 -> 严格 OOS 回测 -> 导出校验 -> API/CLI。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agents.timesfm import TimesFMAgent
from src.configs.settings import Settings
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState
from src.routers.v1 import api_v1_router
from src.runtime.dependencies import RuntimeDependencies
from src.runtime.timesfm_pipeline import TimesFMPipelineCommand


class _DeterministicEvaluator:
    def __init__(self) -> None:
        self.device = "cpu"

    def predict_batch(self, contexts: list[np.ndarray], horizon: int, **kwargs: Any) -> list[Any]:
        outputs: list[Any] = []
        for ctx in contexts:
            last = float(ctx[-1])
            slope = 0.0015 if len(ctx) >= 2 and ctx[-1] >= ctx[-2] else -0.0015
            path = np.maximum(np.linspace(last, last * (1.0 + slope * max(1, horizon)), horizon, dtype=np.float32), 1.0)
            quantiles = np.stack([path * (0.985 + 0.004 * q) for q in range(9)], axis=1)
            outputs.append(SimpleNamespace(forecast=path, quantiles=quantiles))
        return outputs


def _seed_quotes(runtime: RuntimeDependencies, count: int = 240) -> None:
    start = datetime(2026, 3, 2, 1, 0, tzinfo=UTC)
    for i in range(count):
        ts = start + timedelta(hours=i)
        mid = 625.0 + 16.0 * math.sin(i / 5.0) + 0.05 * i
        runtime.quotes.save_quote(
            QuoteRaw(
                quote_id=f"q_e2e_{i:04d}",
                source_id="CMBC",
                product_code="GOLD_ACCUMULATION",
                bid_price=round(mid - 0.3, 4),
                ask_price=round(mid + 0.3, 4),
                quote_at=ts,
                received_at=ts,
                available_at=ts,
                is_tradable=True,
                quality_state=DataQualityState.NORMAL,
            )
        )


def _make_runtime(tmp_path: Path) -> RuntimeDependencies:
    rt = RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            timesfm_enabled=True,
            timesfm_device="cpu",
            news_enabled=False,
            live_quotes_enabled=False,
            market_bars_enabled=False,
            news_sync_enabled=False,
            jd_holdings_sync_enabled=False,
            jd_proactive_auth_enabled=False,
        )
    )
    rt.timesfm._evaluator = _DeterministicEvaluator()
    rt.timesfm._loaded_device = "cpu"
    return rt


def test_timesfm_rest_api_full_pipeline_and_security(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    _seed_quotes(runtime, count=240)
    app = FastAPI()
    app.state.runtime = runtime
    app.include_router(api_v1_router)
    client = TestClient(app)

    try:
        # 1. 状态与路径穿越拦截
        status = client.get("/api/v1/timesfm/status")
        assert status.status_code == 200
        assert status.json()["experiment_count"] == 0

        models_status = client.get("/api/v1/models/status")
        assert models_status.status_code == 200
        assert "timesfm_pipeline" in models_status.json()

        bad_path = client.post(
            "/api/v1/timesfm/prepare",
            json={"source_mode": "quotes", "output_dir": "../../outside"},
        )
        assert bad_path.status_code == 400

        # 2. 数据准备 -> 训练 -> 严格 OOS 回测 -> 独立导出（一键流水线）
        res = client.post(
            "/api/v1/timesfm/pipeline",
            json={
                "prepare": {
                    "source_mode": "quotes",
                    "product_code": "GOLD_ACCUMULATION",
                    "freq": "1h",
                    "context_length": 32,
                    "horizons": ["1h"],
                    "output_dir": "timesfm_ds_e2e",
                },
                "train": {
                    "experiment_name": "e2e_api_exp",
                    "train_mode": "multifactor_head",
                    "head_model_type": "lgb",
                    "n_splits": 2,
                    "holdout_ratio": 0.25,
                    "num_boost_round": 15,
                    "confidence_level": 0.85,
                },
                "backtest": {
                    "initial_cash": 100000.0,
                    "trade_grams_per_order": 2.0,
                },
                "output_model_dir": "models/timesfm_e2e_v1",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        run_id = body["train"]["experiment"]["run_id"]
        assert body["backtest"]["run_id"] == run_id
        assert "1h" in body["backtest"]["horizon_metrics"]
        assert body["backtest"]["backtest_summary"]["quote_count"] > 0
        assert body["backtest"]["mapped_horizons"] == {"1w": "1h", "1d": "1h"}
        assert body["export"]["verified"] is True

        # 校验 SHA-256 导出清单真实匹配
        exported_dir = tmp_path / "models/timesfm_e2e_v1"
        checksums = json.loads((exported_dir / "export_checksum.json").read_text(encoding="utf-8"))
        assert "manifest.json" in checksums
        assert hashlib.sha256((exported_dir / "manifest.json").read_bytes()).hexdigest() == checksums["manifest.json"]

        # 3. 禁止样本内伪回测（start_time <= calibrated_until 必须返回 400）
        calibrated_at = body["train"]["trained_model_info"]["horizons"]["1h"]["calibrated_until"]
        in_sample_bt = client.post(
            "/api/v1/timesfm/backtest",
            json={"run_id": run_id, "start_time": calibrated_at},
        )
        assert in_sample_bt.status_code == 400

        # 4. 禁止重复导出覆盖已有模型目录（400）及导出到数据根目录或保留目录（400）
        dup_export = client.post(
            "/api/v1/timesfm/export",
            json={"run_id": run_id, "output_model_dir": "models/timesfm_e2e_v1"},
        )
        assert dup_export.status_code == 400
        root_export = client.post(
            "/api/v1/timesfm/export",
            json={"run_id": run_id, "output_model_dir": "."},
        )
        assert root_export.status_code == 400
        reserved_export = client.post(
            "/api/v1/timesfm/export",
            json={"run_id": run_id, "output_model_dir": "jd-browser-profile/leak"},
        )
        assert reserved_export.status_code == 400
        blank_pipeline = client.post(
            "/api/v1/timesfm/pipeline",
            json={"output_model_dir": "   "},
        )
        assert blank_pipeline.status_code == 422

        # 5. 并发任务锁冲突应返回 409
        assert runtime.timesfm_lock.acquire(blocking=False)
        try:
            conflict = client.post(
                "/api/v1/timesfm/prepare",
                json={"source_mode": "quotes", "output_dir": "ds_conflict"},
            )
            assert conflict.status_code == 409
        finally:
            runtime.timesfm_lock.release()

        # 6. 实验列表按 run_id 去重，导出后仍为 1 条且已更新 exported_model_dir
        exps = client.get("/api/v1/timesfm/experiments")
        assert exps.status_code == 200
        exp_payload = exps.json()
        assert exp_payload["total"] == 1
        assert len(exp_payload["experiments"]) == 1
        assert exp_payload["experiments"][0]["run_id"] == run_id
        assert exp_payload["experiments"][0]["exported_model_dir"] == str(exported_dir)
    finally:
        asyncio.run(runtime.close())


def test_timesfm_cli_step_by_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _make_runtime(tmp_path)
    _seed_quotes(runtime, count=200)
    asyncio.run(runtime.close())

    # 注入轻量评估器，避免真实加载 HuggingFace 权重
    monkeypatch.setattr(
        TimesFMAgent,
        "_build_evaluator",
        lambda self, checkpoint, device: _DeterministicEvaluator(),
    )
    settings = Settings(
        data_dir=str(tmp_path),
        timesfm_enabled=True,
        timesfm_device="cpu",
        news_enabled=False,
        live_quotes_enabled=False,
        market_bars_enabled=False,
        news_sync_enabled=False,
        jd_holdings_sync_enabled=False,
        jd_proactive_auth_enabled=False,
    )

    assert TimesFMPipelineCommand.run(["status"], settings=settings) == 0
    assert (
        TimesFMPipelineCommand.run(
            [
                "prepare",
                "--source",
                "quotes",
                "--product",
                "GOLD_ACCUMULATION",
                "--freq",
                "1h",
                "--context-length",
                "32",
                "--horizons",
                "1h",
                "--output-dir",
                str(tmp_path / "cli_ds"),
            ],
            settings=settings,
        )
        == 0
    )
    assert (
        TimesFMPipelineCommand.run(
            [
                "train",
                "--dataset-dir",
                str(tmp_path / "cli_ds"),
                "--head",
                "ridge",
                "--splits",
                "2",
                "--holdout-ratio",
                "0.2",
                "--confidence",
                "0.85",
            ],
            settings=settings,
        )
        == 0
    )
    assert TimesFMPipelineCommand.run(["backtest"], settings=settings) == 0
    assert (
        TimesFMPipelineCommand.run(
            ["export", "--output-dir", str(tmp_path / "cli_model")],
            settings=settings,
        )
        == 0
    )
    assert TimesFMPipelineCommand.run(["experiments"], settings=settings) == 0
    assert TimesFMPipelineCommand.run(["experiments", "--limit", "0"], settings=settings) == 2
    assert (tmp_path / "cli_model" / "export_checksum.json").is_file()
