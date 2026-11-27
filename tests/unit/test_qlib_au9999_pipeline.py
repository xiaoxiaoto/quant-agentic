"""Au99.99 Qlib 全链路单元测试：PIT 宏观导出、16 因子预设、滚动训练、择时回测与盘前信号。"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.agents.qlib import QlibAgent
from src.contracts.domains.market_bar import (
    MACRO_DXY_PRODUCT,
    MACRO_US10Y_PRODUCT,
    MACRO_USDCNY_PRODUCT,
    SGE_AU9999_PRODUCT,
    XAUUSD_MERGED_SOURCE,
    XAUUSD_PRODUCT,
    KLineBar,
)
from src.contracts.domains.product import FeeRule
from src.contracts.enums.trading import DecisionAction, HoldingPeriod
from src.contracts.schemas.qlib import (
    QlibBacktestRequest,
    QlibExportRequest,
    QlibSignalRequest,
    QlibTrainRequest,
)
from src.repositories.market_bar import MarketBarRepository
from src.repositories.qlib_experiment import QlibExperimentRepository
from src.repositories.quote import QuoteRepository
from src.services.execution.shadow_ledger import ShadowLedger
from src.services.market.session_calendar import SessionCalendar
from src.services.qlib.backtest_service import QlibBacktestService
from src.services.qlib.data_exporter import QlibDataExporter
from src.services.qlib.factor_library import QlibFactorLibrary
from src.services.qlib.research_service import QlibResearchService
from src.services.qlib.runtime_bridge import QlibRuntimeBridge
from src.services.qlib.signal_service import QlibSignalService
from src.services.risk.alert_service import AlertService

_SHANGHAI = ZoneInfo("Asia/Shanghai")


class _DummyBarCollector:
    """轻量测试替身，直接委托 MarketBarRepository 查询 K 线。"""

    def __init__(self, repo: MarketBarRepository) -> None:
        self._repo = repo

    def query_klines(
        self,
        product_code: str,
        interval: str,
        *,
        source: Any = None,
        limit: int | None = 500,
    ) -> list[KLineBar]:
        return self._repo.query_klines(product_code, interval, source=source, limit=limit)


def _read_qlib_bin(path: Path) -> np.ndarray:
    raw = np.fromfile(path, dtype="<f")
    return raw[1:]


def test_au9999_factor_preset_and_label_modes() -> None:
    """验证 au9999_timing 预设包含 16 个精选因子且不含成交量噪音，并支持毛/净收益标签。"""
    specs = QlibFactorLibrary.get_factors("au9999_timing", freq="day")
    assert len(specs) == 16
    names = {spec.name for spec in specs}
    assert {"roc_5", "roc_20", "ma_5", "ma_20", "beta_10", "rsqr_10", "beta_20", "rsqr_20"} <= names
    assert {"std_10", "std_20", "klen", "rsv_20", "xau_ret", "usdcny_ret", "dxy_ret", "us10y_diff"} <= names
    for spec in specs:
        assert "$volume" not in spec.expression

    gross_expr = QlibFactorLibrary.build_net_return_label_expression(
        horizon=HoldingPeriod.D1,
        freq="day",
        fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.004),
        source_type="local_sqlite",
        label_mode="gross",
    )
    assert gross_expr == "(Ref($close, -1) / $close - 1)"


def test_exporter_au9999_macro_pit_alignment_prevents_lookahead(tmp_path: Path) -> None:
    """验证 SGE-Au99.99 日频导出按北京时间 08:30 严格 PIT 对齐外盘与宏观特征，杜绝当日美盘前视泄漏。"""
    db_path = tmp_path / "pit.sqlite3"
    bar_repo = MarketBarRepository(db_path)
    now_utc = datetime.now(UTC)

    days = [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)]
    sge_bars: list[KLineBar] = []
    xau_bars: list[KLineBar] = []
    fx_bars: list[KLineBar] = []
    dxy_bars: list[KLineBar] = []
    us10y_bars: list[KLineBar] = []

    # 故意在第 3 天美盘（2026-11-04 22:00 UTC，晚于 2026-11-04 08:30 CST）制造极端跳变
    xau_closes = [2600.0, 2652.0, 5000.0]
    fx_closes = [7.10, 7.171, 9.50]
    dxy_closes = [100.0, 102.0, 150.0]
    us10y_vals = [4.00, 4.15, 8.00]

    for idx, day in enumerate(days):
        sge_start = datetime.combine(day, time.min, _SHANGHAI).astimezone(UTC)
        sge_end = datetime.combine(day, time(15, 30), _SHANGHAI).astimezone(UTC)
        sge_bars.append(
            KLineBar(
                product_code=SGE_AU9999_PRODUCT,
                interval="day",
                source="sge_daily",
                bar_time=sge_start,
                trade_date=day.isoformat(),
                open_price=620.0 + idx,
                high_price=625.0 + idx,
                low_price=618.0 + idx,
                close_price=622.0 + idx,
                collected_at=now_utc,
                available_at=sge_end,
            )
        )
        macro_start = datetime.combine(day, time.min, UTC)
        macro_end = datetime.combine(day, time(22, 0), UTC)
        xau_bars.append(
            KLineBar(
                product_code=XAUUSD_PRODUCT,
                interval="day",
                source=XAUUSD_MERGED_SOURCE,
                bar_time=macro_start,
                trade_date=day.isoformat(),
                open_price=xau_closes[idx],
                high_price=xau_closes[idx],
                low_price=xau_closes[idx],
                close_price=xau_closes[idx],
                collected_at=now_utc,
                available_at=macro_end,
            )
        )
        for prod, vals, bucket in (
            (MACRO_USDCNY_PRODUCT, fx_closes, fx_bars),
            (MACRO_DXY_PRODUCT, dxy_closes, dxy_bars),
            (MACRO_US10Y_PRODUCT, us10y_vals, us10y_bars),
        ):
            bucket.append(
                KLineBar(
                    product_code=prod,
                    interval="day",
                    source="macro_daily",
                    bar_time=macro_start,
                    trade_date=day.isoformat(),
                    open_price=vals[idx],
                    high_price=vals[idx],
                    low_price=vals[idx],
                    close_price=vals[idx],
                    collected_at=now_utc,
                    available_at=macro_end,
                )
            )

    bar_repo.save_klines_with_counts({"day": sge_bars + xau_bars + fx_bars + dxy_bars + us10y_bars})

    exporter = QlibDataExporter(
        quotes=QuoteRepository(storage_dir=tmp_path, database_path=db_path),
        calendar=SessionCalendar(),
        market_bars=_DummyBarCollector(bar_repo),  # type: ignore[arg-type]
    )
    out_dir = tmp_path / "qlib_au9999_day"
    report = exporter.export_local_dataset(
        QlibExportRequest(
            source_mode="kline",
            instruments=[SGE_AU9999_PRODUCT],
            freq="day",
            kline_interval="day",
            kline_source="sge_daily",
            include_macro_features=True,
        ),
        target_dir=out_dir,
    )
    assert {"factor", "xau_ret", "usdcny_ret", "dxy_ret", "us10y_diff"} <= set(report.manifest.fields)

    inst_dir = out_dir / "features" / SGE_AU9999_PRODUCT.lower()
    factors = _read_qlib_bin(inst_dir / "factor.day.bin")
    xau_rets = _read_qlib_bin(inst_dir / "xau_ret.day.bin")
    dxy_rets = _read_qlib_bin(inst_dir / "dxy_ret.day.bin")
    us10y_diffs = _read_qlib_bin(inst_dir / "us10y_diff.day.bin")

    assert np.allclose(factors, 1.0)
    # 前两天缺少两条早于盘前 08:30 的观测，应为 NaN
    assert math.isnan(float(xau_rets[0]))
    assert math.isnan(float(xau_rets[1]))
    # 第 3 天（2026-11-04）盘前 08:30 仅能看到 11-02 与 11-03 的收盘，绝不可看到 11-04 晚间的极端跳变
    assert math.isclose(float(xau_rets[2]), 2652.0 / 2600.0 - 1.0, rel_tol=1e-4)
    assert math.isclose(float(dxy_rets[2]), 102.0 / 100.0 - 1.0, rel_tol=1e-4)
    assert math.isclose(float(us10y_diffs[2]), 0.15, abs_tol=1e-4)


def test_rolling_train_robust_zscore_backtest_and_signal(tmp_path: Path) -> None:
    """验证滚动训练 + 折内 RobustZScoreNorm + 单标的择时回测 + 盘前信号与提醒发布。"""
    rng = np.random.default_rng(7)
    n_days = 160
    start_dt = datetime(2025, 1, 2, tzinfo=UTC)
    dates = [start_dt + timedelta(days=i) for i in range(n_days)]
    specs = QlibFactorLibrary.get_factors("au9999_timing", freq="day")
    n_feats = len(specs)

    x_mat = rng.normal(0.0, 1.0, size=(n_days, n_feats))
    # 注入极端离群值验证 RobustZScoreNorm 不会导致数值异常
    x_mat[10, 0] = 500.0
    signal_driver = 0.008 * x_mat[:, 0] + 0.006 * x_mat[:, 12] + rng.normal(0.0, 0.004, size=n_days)
    closes = 600.0 * np.cumprod(1.0 + np.clip(signal_driver * 0.5, -0.03, 0.03))
    labels = signal_driver

    class _FakeBridge(QlibRuntimeBridge):
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
            idx = pd.MultiIndex.from_tuples([(instruments[0], dt) for dt in dates])
            if len(expressions) == n_feats + 1 and expressions[-1] == "$close":
                # 盘前信号查询：额外附加一条晚于校准截止时间的最新观测
                latest_dt = dates[-1] + timedelta(days=2)
                latest_idx = pd.MultiIndex.from_tuples([(instruments[0], latest_dt)])
                latest_row = np.concatenate([[3.0] * n_feats, [float(closes[-1] * 1.01)]])
                return pd.DataFrame([latest_row], index=latest_idx)
            if len(expressions) == n_feats + 2:
                data = np.column_stack([x_mat, labels, closes])
            else:
                data = np.column_stack([x_mat, labels])
            return pd.DataFrame(data, index=idx)

        def log_recorder_metrics(self, **kwargs: Any) -> str | None:
            return None

    provider_dir = tmp_path / "qlib_au9999_day"
    provider_dir.mkdir()
    exp_dir = tmp_path / "qlib_experiments"
    exporter = QlibDataExporter(
        quotes=QuoteRepository(storage_dir=tmp_path, database_path=tmp_path / "q.sqlite3"),
        calendar=SessionCalendar(),
    )
    research = QlibResearchService(
        exporter=exporter,
        bridge=_FakeBridge(),
        provider_uri=provider_dir,
        experiment_dir=exp_dir,
        experiment_repo=QlibExperimentRepository(exp_dir),
        fee_rule=FeeRule(buy_fee_rate=0.0, sell_fee_rate=0.0003),
    )

    model_dir = tmp_path / "models" / "au9999_v1"
    train_report = research.train_and_record(
        QlibTrainRequest(
            experiment_name="au9999_rolling_test",
            instruments=[SGE_AU9999_PRODUCT],
            freq="day",
            model_type="lgb",
            factor_preset="au9999_timing",
            label_mode="gross",
            horizons=[HoldingPeriod.D1],
            window_mode="rolling",
            train_window_bars=90,
            step_bars=25,
            use_robust_zscore=True,
            num_boost_round=25,
            lgb_max_depth=3,
            lgb_num_leaves=8,
            output_model_dir=str(model_dir),
        )
    )
    assert len(train_report.walk_forward_folds["1d"]) >= 2
    assert (model_dir / "manifest.json").exists()

    # 验证单标的阈值择时回测
    bt_service = QlibBacktestService(research)
    bt_report = bt_service.run_backtest(
        QlibBacktestRequest(
            instrument=SGE_AU9999_PRODUCT,
            freq="day",
            factor_preset="au9999_timing",
            label_mode="gross",
            horizon=HoldingPeriod.D1,
            window_mode="rolling",
            train_window_bars=90,
            step_bars=25,
            use_robust_zscore=True,
            num_boost_round=25,
            buy_thresh=0.0005,
            sell_thresh=-0.0005,
            trade_unit_grams=10.0,
            open_cost=0.0003,
            close_cost=0.0003,
        )
    )
    assert bt_report.summary.bars_evaluated > 0
    assert len(bt_report.equity_curve) == bt_report.summary.bars_evaluated
    for tr in bt_report.trades:
        assert tr.grams % 10.0 == 0.0

    # 验证盘前信号生成与 AlertService 人工复核队列推送
    agent = QlibAgent(model_dir, allow_non_serving=True)
    assert agent.get_model_info()["factor_preset"] == "au9999_timing"

    alerts = AlertService(fee_rule=FeeRule())
    ledger = ShadowLedger(initial_cash=100000.0, fee_rule=FeeRule())
    sig_service = QlibSignalService(
        research,
        alerts=alerts,
        ledger=ledger,
        default_model_dir=str(model_dir),
        max_signal_age=None,
    )
    sig_resp = sig_service.generate_signal(
        QlibSignalRequest(
            instrument=SGE_AU9999_PRODUCT,
            freq="day",
            horizon=HoldingPeriod.D1,
            buy_thresh=-1.0,  # 强制触发 BUY 验证提醒发布链路
            sell_thresh=-2.0,
            publish_alert=True,
            trade_grams=10.0,
        )
    )
    assert sig_resp.signal.action == DecisionAction.BUY
    assert sig_resp.signal.alert_id is not None
    assert len(sig_resp.signal.feature_snapshot) == 16

    # 空仓状态下即使触发 SELL 信号，也不应生成无效卖出复核提醒
    sell_resp = sig_service.generate_signal(
        QlibSignalRequest(
            instrument=SGE_AU9999_PRODUCT,
            freq="day",
            horizon=HoldingPeriod.D1,
            buy_thresh=2.0,
            sell_thresh=1.0,  # 强制触发 SELL
            publish_alert=True,
            trade_grams=10.0,
        )
    )
    assert sell_resp.signal.action == DecisionAction.SELL
    assert sell_resp.signal.alert_id is None

    # 陈旧数据在启用新鲜度门禁时不应发布复核提醒
    strict_sig_service = QlibSignalService(
        research,
        alerts=alerts,
        ledger=ledger,
        default_model_dir=str(model_dir),
        max_signal_age=timedelta(days=5),
    )
    stale_resp = strict_sig_service.generate_signal(
        QlibSignalRequest(
            instrument=SGE_AU9999_PRODUCT,
            freq="day",
            horizon=HoldingPeriod.D1,
            buy_thresh=-1.0,
            sell_thresh=-2.0,
            publish_alert=True,
            trade_grams=10.0,
        )
    )
    assert stale_resp.signal.action == DecisionAction.BUY
    assert stale_resp.signal.alert_id is None

    # 现金不足（为 0）时 BUY 不应发布复核提醒
    empty_cash_ledger = ShadowLedger(initial_cash=0.0, fee_rule=FeeRule())
    cash_guard_service = QlibSignalService(
        research,
        alerts=alerts,
        ledger=empty_cash_ledger,
        default_model_dir=str(model_dir),
        max_signal_age=None,
    )
    no_cash_resp = cash_guard_service.generate_signal(
        QlibSignalRequest(
            instrument=SGE_AU9999_PRODUCT,
            freq="day",
            horizon=HoldingPeriod.D1,
            buy_thresh=-1.0,
            sell_thresh=-2.0,
            publish_alert=True,
            trade_grams=10.0,
        )
    )
    assert no_cash_resp.signal.action == DecisionAction.BUY
    assert no_cash_resp.signal.alert_id is None


def test_simulate_timing_limit_threshold_and_lot_rounding() -> None:
    """验证涨跌停门禁拦截、10 克/手取整与完整回合胜率统计。"""
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    oos = [
        (t0, 0.01, 0.7, 0.005, 0.01),  # 正常买入（600）
        (t0 + timedelta(days=1), -0.02, 0.2, -0.01, -0.10),  # 跌停 -10% -> 拦截卖出
        (t0 + timedelta(days=2), -0.02, 0.2, -0.01, 0.05),  # 反弹至 630 -> 正常卖出盈利
    ]
    closes = {
        t0: 600.0,
        t0 + timedelta(days=1): 540.0,
        t0 + timedelta(days=2): 630.0,
    }
    req = QlibBacktestRequest(
        buy_thresh=0.001,
        sell_thresh=-0.0005,
        initial_cash=100000.0,
        cash_utilization=0.98,
        trade_unit_grams=10.0,
        open_cost=0.0003,
        close_cost=0.0003,
        limit_threshold=0.08,
    )
    summary, trades, _curve = QlibBacktestService.simulate_timing(
        oos_predictions=oos,
        closes_by_dt=closes,
        request=req,
    )
    assert summary.trade_count == 2
    assert trades[0].action == "BUY"
    assert trades[0].grams == 160.0
    assert trades[1].action == "SELL"
    assert trades[1].timestamp == t0 + timedelta(days=2)
    assert summary.round_trip_count == 1
    assert summary.win_rate == 1.0
    assert summary.total_return > 0.0


def test_qlib_threshold_order_and_kline_source_priority(tmp_path: Path) -> None:
    """验证倒挂阈值校验与多来源 K 线导出优先级去重（jd_public_kline 优先于 jd_sim_kline）。"""
    import pytest
    from pydantic import ValidationError

    from src.configs.model import ModelSettings
    from src.contracts.domains.market_bar import KLineBar

    with pytest.raises(ValidationError):
        QlibBacktestRequest(buy_thresh=-0.002, sell_thresh=0.001)
    with pytest.raises(ValidationError):
        QlibSignalRequest(buy_thresh=-0.002, sell_thresh=0.001)
    with pytest.raises(ValidationError):
        ModelSettings(qlib_au9999_buy_thresh=-0.002, qlib_au9999_sell_thresh=0.001)

    exporter = QlibDataExporter(
        quotes=QuoteRepository(storage_dir=tmp_path, database_path=tmp_path / "prio.sqlite3"),
        calendar=SessionCalendar(),
    )
    day_ts = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)
    public_bar = KLineBar(
        product_code=SGE_AU9999_PRODUCT,
        interval="day",
        source="jd_public_kline",
        bar_time=day_ts,
        trade_date="2026-10-10",
        open_price=600.0,
        high_price=605.0,
        low_price=598.0,
        close_price=602.0,
        collected_at=datetime(2026, 10, 10, 8, 0, tzinfo=UTC),
        available_at=datetime(2026, 10, 10, 7, 30, tzinfo=UTC),
    )
    sim_bar = KLineBar(
        product_code=SGE_AU9999_PRODUCT,
        interval="day",
        source="jd_sim_kline",
        bar_time=day_ts + timedelta(hours=1),
        trade_date="2026-10-10",
        open_price=500.0,
        high_price=505.0,
        low_price=498.0,
        close_price=501.0,
        collected_at=datetime(2026, 10, 10, 9, 0, tzinfo=UTC),
        available_at=datetime(2026, 10, 10, 8, 30, tzinfo=UTC),
    )
    rows = exporter._aggregate_klines(
        [public_bar, sim_bar],
        freq="day",
        spread_pct=0.002,
        basis_times=[],
        basis_prices=[],
    )
    assert rows["2026-10-10"]["close"] == 602.0


def test_simulate_timing_does_not_misclassify_gap_as_limit_move() -> None:
    """跨折空洞导致相邻预测跨越多日时，不得将累计涨跌幅误判为单日涨跌停。"""
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    t_gap = t0 + timedelta(days=30)
    # 30 天内累计上涨 +15%，但若按单日涨跌停 8% 判定则会错误拦截买入
    oos = [
        (t0, 0.0, 0.5, 0.0, 0.0),
        (t_gap, 0.02, 0.8, 0.01, 0.01),
    ]
    closes = {t0: 600.0, t_gap: 690.0}
    req = QlibBacktestRequest(
        instrument=SGE_AU9999_PRODUCT,
        buy_thresh=0.001,
        sell_thresh=-0.001,
        limit_threshold=0.08,
    )
    _, trades, _ = QlibBacktestService.simulate_timing(oos_predictions=oos, closes_by_dt=closes, request=req)
    assert len(trades) == 1
    assert trades[0].action == "BUY"


def test_timesfm_backtest_request_and_repo_limit_bounds(tmp_path: Path) -> None:
    """验证 TimesFMBacktestRequest 时间顺序校验与仓储负值 limit 防护。"""
    import pytest

    from src.contracts.schemas.timesfm_multifactor import TimesFMBacktestRequest
    from src.repositories.reference_quote import ReferenceQuoteRepository

    with pytest.raises(ValueError, match="回测起始时间不得晚于结束时间"):
        TimesFMBacktestRequest(
            start_time=datetime(2026, 11, 10, tzinfo=UTC),
            end_time=datetime(2026, 11, 1, tzinfo=UTC),
        )

    bar_repo = MarketBarRepository(tmp_path / "bounds.sqlite3")
    with pytest.raises(ValueError, match="limit"):
        bar_repo.query_klines(SGE_AU9999_PRODUCT, "day", limit=-1)
    assert bar_repo.query_klines(SGE_AU9999_PRODUCT, "day", limit=0) == []

    ref_repo = ReferenceQuoteRepository(
        storage_dir=tmp_path,
        database_path=tmp_path / "bounds.sqlite3",
        import_jsonl_on_read=False,
    )
    with pytest.raises(ValueError, match="limit"):
        ref_repo.query_series(SGE_AU9999_PRODUCT, limit=-1)
    assert ref_repo.query_series(SGE_AU9999_PRODUCT, limit=0) == []
