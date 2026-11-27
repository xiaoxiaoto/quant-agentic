"""TimesFM 多因子训练、主干微调、独立校准与线上 Agent 接入单元测试。"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from src.agents.timesfm import TimesFMAgent
from src.agents.timesfm_multifactor import TimesFMMultiFactorAgent
from src.configs.settings import Settings
from src.contracts.domains.feature import FeatureSet
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState, HoldingPeriod
from src.contracts.schemas.timesfm_multifactor import TimesFMPrepareRequest, TimesFMTrainRequest
from src.runtime.dependencies import RuntimeDependencies


class _StubEvaluator:
    """确定性轻量评估器：记录协变量形状并返回带分位数的平滑预测。"""

    def __init__(self) -> None:
        self.device = "cpu"
        self.calls: list[dict[str, Any]] = []

    def predict_batch(
        self,
        contexts: list[np.ndarray],
        horizon: int,
        **kwargs: Any,
    ) -> list[Any]:
        self.calls.append({"batch": len(contexts), "horizon": horizon, "keys": set(kwargs.keys())})
        outputs: list[Any] = []
        po_list = kwargs.get("past_only_covariates")
        for idx, ctx in enumerate(contexts):
            last = float(ctx[-1])
            slope = 0.002 if len(ctx) >= 2 and ctx[-1] >= ctx[-2] else -0.002
            if po_list is not None:
                slope += 0.001 * float(np.sign(po_list[idx][0, -1]))
            path = np.linspace(last, last * (1.0 + slope * max(1, horizon)), horizon, dtype=np.float32)
            path = np.maximum(path, 1.0)
            quantiles = np.stack([path * (0.98 + 0.005 * q) for q in range(9)], axis=1)
            outputs.append(SimpleNamespace(forecast=path, quantiles=quantiles))
        return outputs


def _seed_quotes(runtime: RuntimeDependencies, count: int = 200) -> None:
    start = datetime(2026, 3, 2, 1, 0, tzinfo=UTC)
    for i in range(count):
        ts = start + timedelta(hours=i)
        mid = 620.0 + 18.0 * math.sin(i / 5.0) + 0.04 * i
        runtime.quotes.save_quote(
            QuoteRaw(
                quote_id=f"q_tr_{i:04d}",
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


def _runtime(tmp_path: Path, evaluator: Any = None) -> RuntimeDependencies:
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
    if evaluator is not None:
        rt.timesfm._evaluator = evaluator
        rt.timesfm._loaded_device = "cpu"
    return rt


@pytest.mark.parametrize("head_type", ["lgb", "ridge"])
def test_multifactor_head_training_walk_forward_and_serving_agent(tmp_path: Path, head_type: str) -> None:
    evaluator = _StubEvaluator()
    rt = _runtime(tmp_path, evaluator)
    _seed_quotes(rt, count=210)

    prep = rt.timesfm_pipeline.prepare_dataset(
        TimesFMPrepareRequest(
            source_mode="quotes",
            product_code="GOLD_ACCUMULATION",
            freq="1h",
            context_length=32,
            horizons=[HoldingPeriod.H1],
        )
    )
    model_out = f"models/tfm_{head_type}_v1"
    report = rt.timesfm_pipeline.train_and_record(
        TimesFMTrainRequest(
            dataset_dir=prep.manifest.dataset_dir,
            train_mode="multifactor_head",
            head_model_type=head_type,  # type: ignore[arg-type]
            n_splits=2,
            holdout_ratio=0.2,
            num_boost_round=15,
            confidence_level=0.85,
            use_covariates=True,
            output_model_dir=model_out,
        )
    )

    assert report.experiment.head_model_type == head_type
    assert report.experiment.holdout_start_at is not None
    assert len(report.walk_forward_folds["1h"]) >= 1
    assert any("past_only_covariates" in call["keys"] for call in evaluator.calls)
    assert any("past_future_covariates" in call["keys"] for call in evaluator.calls)

    # 加载导出产物并验证 PIT 时间门禁与分布缓存推理
    exported = tmp_path / model_out
    agent = TimesFMMultiFactorAgent(exported, timesfm_agent=rt.timesfm)
    info = agent.get_model_info()
    assert info["model_version"] == "timesfm_multifactor_v1"
    assert info["production_approved"] is False

    # 严格拒绝 <= calibrated_until 的推理请求
    with pytest.raises(ValueError, match="校准标签结束时间"):
        agent.predict(
            FeatureSet(quote_id="q_past", as_of=agent.calibrated_until),
            HoldingPeriod.H1,
        )

    # 绑定 TimesFMAgent 缓存后应能读取完整 7 维分布特征并预测
    future_as_of = agent.calibrated_until + timedelta(hours=1)
    quotes = [
        QuoteRaw(
            quote_id=f"live_{i}",
            source_id="CMBC",
            product_code="GOLD_ACCUMULATION",
            bid_price=640.0 + 0.1 * i,
            ask_price=640.6 + 0.1 * i,
            quote_at=future_as_of - timedelta(hours=39 - i),
            received_at=future_as_of - timedelta(hours=39 - i),
            available_at=future_as_of - timedelta(hours=39 - i),
            is_tradable=True,
        )
        for i in range(40)
    ]
    tf_ret = rt.timesfm.predict_feature(quotes, HoldingPeriod.H1, as_of=future_as_of)
    assert tf_ret is not None
    cached_dist = rt.timesfm.get_cached_distribution(future_as_of, HoldingPeriod.H1)
    assert cached_dist is not None

    pred = agent.predict(
        FeatureSet(quote_id="q_future", as_of=future_as_of, timesfm_forecast_return=tf_ret),
        HoldingPeriod.H1,
    )
    assert pred.model_version == "timesfm_multifactor_v1"
    assert pred.is_calibrated is True
    assert pred.net_return_lcb <= pred.expected_net_return

    # 独立部署（未传 timesfm_agent）也能安全降级预测
    standalone = TimesFMMultiFactorAgent(exported)
    pred_standalone = standalone.predict(FeatureSet(quote_id="q_s", as_of=future_as_of), HoldingPeriod.H1)
    assert math.isfinite(pred_standalone.expected_net_return)

    # 验证 RuntimeDependencies 对 PREDICTOR_BACKEND=timesfm 的装配
    rt_serving = RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            predictor_backend="timesfm",
            timesfm_model_dir=str(exported),
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
    assert isinstance(rt_serving.model, TimesFMMultiFactorAgent)


def test_backbone_finetune_tiny_torch_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """使用微型 TimesFM3Torch 真实执行 PyTorch Pinball Loss 微调、保存权重与重载推理。"""
    from timesfm3.torch import configs as tfm_configs
    from timesfm3.torch import model as tfm_model
    from timesfm3.torch.evaluator import TimesFM3Evaluator

    res_cfg = tfm_configs.ResidualBlockConfig(hidden_dims=32, output_dims=32, use_bias=False, activation="relu")
    tf_cfg = tfm_configs.StackedTransformersConfig(
        num_layers=1,
        transformer=tfm_configs.TransformerConfig(
            model_dims=32,
            hidden_dims=32,
            num_heads=4,
            attention_norm="rms",
            feedforward_norm="rms",
            qk_norm="rms",
            use_rope_seq=True,
            use_rope_var=True,
            use_bias=False,
            ff_activation="relu",
            deterministic=True,
        ),
    )
    tiny_net = tfm_model.TimesFM3Torch(
        input_patch_len=32,
        output_patch_len=64,
        residual_block_config=res_cfg,
        transformer_config=tf_cfg,
        use_variate_attention=True,
        use_iterative_cpm_revin=False,
    )
    tiny_net.eval()

    class _TinyEvaluator(TimesFM3Evaluator):
        def _init_model(self) -> None:
            import torch

            self.device = torch.device("cpu")
            ckpt = self.config.checkpoint_path
            if Path(ckpt).is_dir():
                self.model = tfm_model.TimesFM3Torch.from_pretrained(ckpt, local_files_only=True)
            else:
                self.model = tiny_net
            self.model.to(self.device)
            self.model.eval()

    monkeypatch.setattr("timesfm3.TimesFM3Evaluator", _TinyEvaluator)
    tiny_eval = _TinyEvaluator(
        checkpoint_path="tiny-in-memory",
        per_core_batch_size=4,
        device="cpu",
        use_iterative_cpm_revin=False,
    )

    rt = _runtime(tmp_path, tiny_eval)
    _seed_quotes(rt, count=210)
    prep = rt.timesfm_pipeline.prepare_dataset(
        TimesFMPrepareRequest(
            source_mode="quotes",
            product_code="GOLD_ACCUMULATION",
            freq="1h",
            context_length=32,
            horizons=[HoldingPeriod.H1],
        )
    )
    report = rt.timesfm_pipeline.train_and_record(
        TimesFMTrainRequest(
            dataset_dir=prep.manifest.dataset_dir,
            train_mode="backbone_finetune",
            head_model_type="ridge",
            n_splits=1,
            holdout_ratio=0.15,
            finetune_epochs=1,
            finetune_batch_size=8,
            unfreeze_last_layers=1,
            batch_size=16,
            use_covariates=True,
            output_model_dir="models/tfm_finetuned_v1",
        )
    )

    ft = report.experiment.finetune_summary
    assert ft.enabled is True
    assert ft.checkpoint_saved is True
    assert ft.trainable_params > 0
    assert ft.final_loss is not None
    assert math.isfinite(ft.final_loss)

    exported = tmp_path / "models/tfm_finetuned_v1"
    assert (exported / "timesfm_checkpoint" / "config.json").is_file()
    agent = TimesFMMultiFactorAgent(exported)
    assert agent.checkpoint_dir == (exported / "timesfm_checkpoint").resolve()
    # 线上共享 TimesFMAgent 不应被微调污染或切换权重
    assert rt.timesfm._evaluator is tiny_eval


def test_timesfm_batch_validation_manifest_guard_and_single_pass_backtest(tmp_path: Path) -> None:
    # 1. predict_multifactor_batch 边界校验：空列表不触发加载、空/非有限/非正输入抛 ValueError
    agent_no_load = TimesFMAgent(is_enabled=False)
    assert agent_no_load.predict_multifactor_batch([], 4) == []
    with pytest.raises(ValueError, match="有限"):
        agent_no_load.predict_multifactor_batch([np.array([100.0, float("inf")])], 4)
    with pytest.raises(ValueError, match="有限"):
        agent_no_load.predict_multifactor_batch([np.array([], dtype=np.float32)], 4)

    # 1b. finetune_backbone 入口参数与数据有限性校验
    from src.contracts.exceptions import TimesFMTrainingError

    with pytest.raises(TimesFMTrainingError, match="学习率"):
        agent_no_load.finetune_backbone(
            contexts=[np.array([100.0, 101.0], dtype=np.float32)],
            targets=[np.array([102.0], dtype=np.float32)],
            steps=1,
            checkpoint_dir=tmp_path / "ckpt_bad_lr",
            learning_rate=0.0,
        )
    with pytest.raises(TimesFMTrainingError, match="基准价"):
        agent_no_load.finetune_backbone(
            contexts=[np.array([100.0, 0.0], dtype=np.float32)],
            targets=[np.array([102.0], dtype=np.float32)],
            steps=1,
            checkpoint_dir=tmp_path / "ckpt_bad_base",
        )
    with pytest.raises(TimesFMTrainingError, match="非有限"):
        agent_no_load.finetune_backbone(
            contexts=[np.array([100.0, 101.0], dtype=np.float32)],
            targets=[np.array([float("nan")], dtype=np.float32)],
            steps=1,
            checkpoint_dir=tmp_path / "ckpt_bad_tgt",
        )

    # 2. 损坏或越界 manifest 统一抛 ValueError；fit_calibration_entry 忽略非有限标签
    bad_dir = tmp_path / "bad_model"
    bad_dir.mkdir()
    (bad_dir / "manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON 对象"):
        TimesFMMultiFactorAgent(bad_dir)

    escape_dir = tmp_path / "escape_model"
    escape_dir.mkdir()
    (escape_dir / "manifest.json").write_text(
        '{"version":"timesfm_multifactor_v1","serving_compatible":true,'
        '"head_model_type":"ridge","features":["a"],"horizons":{"1h":{}},'
        '"finetuned_checkpoint_relpath":"../bad_model"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="越出模型目录"):
        TimesFMMultiFactorAgent.read_backbone_config(escape_dir)

    cal_actual = np.linspace(-0.02, 0.02, 24)
    cal_timesfm_point = cal_actual.copy()
    cal_timesfm_point[1] = float("nan")
    cal = TimesFMMultiFactorAgent.fit_calibration_entry(
        cal_predicted=cal_actual,
        cal_raw_scores=np.linspace(-0.5, 0.5, 24),
        cal_actual=cal_actual,
        cal_timesfm_point=cal_timesfm_point,
        confidence_level=0.85,
        training_count=40,
        purged_count=1,
        trained_until=datetime(2026, 3, 1, tzinfo=UTC),
        calibrated_until=datetime(2026, 3, 2, tzinfo=UTC),
        extra_payload={},
    )
    assert math.isfinite(float(cal["timesfm_calibration_slope"]))
    assert math.isfinite(float(cal["timesfm_calibration_intercept"]))

    # 3. predictor_backend=heuristic 时，即使配置了损坏的 timesfm_model_dir 也不得误读覆盖 TimesFMAgent
    rt_heuristic = RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            predictor_backend="heuristic",
            timesfm_model_dir=str(escape_dir),
            timesfm_enabled=False,
            news_enabled=False,
            live_quotes_enabled=False,
            market_bars_enabled=False,
            news_sync_enabled=False,
            jd_holdings_sync_enabled=False,
            jd_proactive_auth_enabled=False,
        )
    )
    assert rt_heuristic.model is None

    # 4. TimesFMBacktestEngine 对相同样本外区间仅执行一次批量推理（不重复推理）
    evaluator = _StubEvaluator()
    rt = _runtime(tmp_path / "bt_once", evaluator)
    _seed_quotes(rt, count=210)
    prep = rt.timesfm_pipeline.prepare_dataset(
        TimesFMPrepareRequest(
            source_mode="quotes",
            product_code="GOLD_ACCUMULATION",
            freq="1h",
            context_length=32,
            horizons=[HoldingPeriod.H1],
        )
    )
    train_rep = rt.timesfm_pipeline.train_and_record(
        TimesFMTrainRequest(
            dataset_dir=prep.manifest.dataset_dir,
            head_model_type="ridge",
            n_splits=1,
            holdout_ratio=0.2,
            confidence_level=0.85,
        )
    )
    evaluator.calls.clear()
    from src.contracts.schemas.timesfm_multifactor import TimesFMBacktestRequest

    bt = rt.timesfm_pipeline.run_backtest(TimesFMBacktestRequest(run_id=train_rep.experiment.run_id, batch_size=64))
    assert bt.backtest_summary is not None
    # 单个持有期 1h 在 OOS 区间内（约 42 条样本，batch_size=64）只应触发 1 次批量预测调用
    assert len(evaluator.calls) == 1
