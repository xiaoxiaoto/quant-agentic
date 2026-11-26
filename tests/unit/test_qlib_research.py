"""Qlib 因子库、降级策略、滚动训练与实验记录单元测试。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from src.agents.qlib import QlibAgent
from src.contracts.domains.feature import FeatureSet
from src.contracts.domains.product import FeeRule
from src.contracts.enums.trading import HoldingPeriod
from src.contracts.exceptions import QlibUnavailableError
from src.contracts.schemas.qlib import QlibFactorEvaluateRequest, QlibTrainRequest
from src.repositories.quote import QuoteRepository
from src.services.market.session_calendar import SessionCalendar
from src.services.qlib.data_exporter import QlibDataExporter
from src.services.qlib.factor_library import QlibFactorLibrary
from src.services.qlib.research_service import QlibResearchService
from src.services.qlib.runtime_bridge import QlibRuntimeBridge


class _FakeFrame:
    """模拟 Qlib D.features 返回的带 MultiIndex 的二维数据帧。"""

    def __init__(self, index: list[tuple[str, datetime]], data: np.ndarray) -> None:
        self.index = index
        self._data = data

    def to_numpy(self, dtype: Any = np.float64) -> np.ndarray:
        return self._data.astype(dtype)


class _UnavailableBridge(QlibRuntimeBridge):
    """模拟未安装 pyqlib 的运行时桥接。"""

    def is_available(self) -> bool:
        return False

    def get_version(self) -> str | None:
        return None

    def fetch_features(self, **_kwargs: Any) -> Any:
        raise QlibUnavailableError("未安装可选依赖 pyqlib")


class _FakeQlibBridge(QlibRuntimeBridge):
    """返回确定性合成因子与净收益标签矩阵的模拟 Qlib 桥接。"""

    def __init__(self, n_rows: int = 240) -> None:
        super().__init__()
        self._n_rows = n_rows

    def is_available(self) -> bool:
        return True

    def get_version(self) -> str | None:
        return "0.9.6-mock"

    def fetch_features(
        self,
        *,
        provider_uri: Path,
        instruments: list[str],
        expressions: list[str],
        freq: str,
        start_time: str | None = None,
        end_time: str | None = None,
        experiment_dir: Path | None = None,
    ) -> Any:
        _ = (provider_uri, freq, start_time, end_time, experiment_dir)
        rng = np.random.default_rng(42)
        n_feats = len(expressions) - 1
        start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        index = [(instruments[0], start + timedelta(hours=i * 6)) for i in range(self._n_rows)]
        x = rng.normal(0.0, 0.015, size=(self._n_rows, n_feats))
        y = 0.6 * x[:, 0] - 0.3 * x[:, 1] + rng.normal(0.0, 0.004, size=self._n_rows)
        data = np.column_stack([x, y])
        return _FakeFrame(index=index, data=data)

    def log_recorder_metrics(
        self,
        *,
        experiment_name: str,
        params: dict[str, Any],
        metrics: dict[str, float],
    ) -> str | None:
        _ = (experiment_name, params, metrics)
        return "rec_mock_001"


def _prepare_mock_dataset_dir(base_dir: Path) -> Path:
    ds_dir = base_dir / "qlib_data"
    (ds_dir / "calendars").mkdir(parents=True)
    (ds_dir / "instruments").mkdir(parents=True)
    (ds_dir / "features" / "cmbc-jcj").mkdir(parents=True)
    (ds_dir / "calendars" / "60min.txt").write_text("2026-01-01 00:00:00\n", encoding="utf-8")
    (ds_dir / "instruments" / "all.txt").write_text(
        "CMBC-JCJ\t2026-01-01 00:00:00\t2026-03-01 00:00:00\n",
        encoding="utf-8",
    )
    np.array([0.0, 620.0], dtype="<f").tofile(ds_dir / "features" / "cmbc-jcj" / "close.60min.bin")
    return ds_dir


def test_factor_library_and_fee_adjusted_labels() -> None:
    """因子预设与银行扣费净收益标签表达式应正确区分本地双边报价与外部数据源。"""
    gold_specs = QlibFactorLibrary.get_factors("gold_core", freq="1h")
    assert any(spec.name == "return_1d" for spec in gold_specs)
    assert any(spec.name == "bank_basis" for spec in gold_specs)

    fee = FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.004)
    local_label = QlibFactorLibrary.build_net_return_label_expression(
        horizon=HoldingPeriod.D1, freq="1h", fee_rule=fee, source_type="local_sqlite"
    )
    ext_label = QlibFactorLibrary.build_net_return_label_expression(
        horizon=HoldingPeriod.D1, freq="day", fee_rule=fee, source_type="external_qlib", spread_pct=0.002
    )
    assert "Ref($bid, -24)" in local_label
    assert "$ask" in local_label
    assert "Ref($close, -1)" in ext_label
    assert "$close" in ext_label


def test_research_service_graceful_degradation_when_pyqlib_missing(tmp_path: Path) -> None:
    """未安装 pyqlib 时状态查询应返回 false，触发因子评估应抛出 QlibUnavailableError。"""
    ds_dir = _prepare_mock_dataset_dir(tmp_path)
    repo = QuoteRepository(storage_dir=tmp_path / "quotes", database_path=tmp_path / "test.sqlite3")
    service = QlibResearchService(
        exporter=QlibDataExporter(quotes=repo, calendar=SessionCalendar()),
        bridge=_UnavailableBridge(),
        provider_uri=ds_dir,
        experiment_dir=tmp_path / "exps",
        fee_rule=FeeRule(),
    )
    status = service.get_status()
    assert status.pyqlib_installed is False
    assert status.local_dataset is not None

    with pytest.raises(QlibUnavailableError):
        service.evaluate_factors(QlibFactorEvaluateRequest(source_type="local_sqlite", freq="1h"))


def test_research_service_evaluate_and_train_with_artifact_export(tmp_path: Path) -> None:
    """模拟 Qlib 引擎下应完整跑通因子评估、Walk-Forward 训练、实验登记与 QlibAgent 加载推理。"""
    ds_dir = _prepare_mock_dataset_dir(tmp_path)
    repo = QuoteRepository(storage_dir=tmp_path / "quotes", database_path=tmp_path / "test.sqlite3")
    service = QlibResearchService(
        exporter=QlibDataExporter(quotes=repo, calendar=SessionCalendar()),
        bridge=_FakeQlibBridge(n_rows=240),
        provider_uri=ds_dir,
        experiment_dir=tmp_path / "exps",
        fee_rule=FeeRule(),
        qlib_enabled=True,
    )

    factor_report = service.evaluate_factors(
        QlibFactorEvaluateRequest(source_type="local_sqlite", freq="1h", factor_preset="gold_core")
    )
    assert len(factor_report.metrics) == 11
    assert factor_report.metrics[0].ic is not None
    assert factor_report.metrics[0].ic > 0.3

    model_out = tmp_path / "models" / "qlib_v1"
    train_report = service.train_and_record(
        QlibTrainRequest(
            experiment_name="gold_test_exp",
            source_type="local_sqlite",
            freq="1h",
            model_type="lgb",
            factor_preset="gold_core",
            horizons=[HoldingPeriod.D1],
            n_splits=2,
            num_boost_round=20,
            output_model_dir=str(model_out),
        )
    )
    assert train_report.experiment.serving_compatible is True
    assert train_report.experiment.qlib_recorder_id == "rec_mock_001"
    assert train_report.sample_counts["1d"] == 240
    assert len(train_report.walk_forward_folds["1d"]) >= 1
    assert train_report.trained_model_info is not None

    experiments = service.list_experiments(limit=10)
    assert len(experiments) == 1
    assert experiments[0].run_id == train_report.experiment.run_id

    # 验证导出的 QlibAgent 产物可直接用于线上特征推理
    agent = QlibAgent(model_out)
    pred = agent.predict(
        FeatureSet(
            quote_id="q_after",
            as_of=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
            return_1d=0.01,
            return_3d=0.02,
            return_1w=0.03,
            realized_volatility=0.15,
            atr=1.2,
            spread=0.5,
            spread_pct=0.001,
            bank_basis=0.002,
            hours_to_weekend_close=40.0,
            hours_since_open=20.0,
            is_weekend_risk_window=False,
        ),
        horizon=HoldingPeriod.D1,
    )
    assert pred.model_version == "qlib_v1"
    assert pred.is_calibrated is True
