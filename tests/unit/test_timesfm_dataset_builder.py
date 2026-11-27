"""TimesFM 多因子数据集构建器单元测试。"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.configs.settings import Settings
from src.contracts.domains.market_bar import XAUUSD_PRODUCT, KLineBar
from src.contracts.domains.product import FeeRule
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.reference_quote import ReferenceQuote
from src.contracts.enums.trading import DataQualityState, HoldingPeriod
from src.contracts.exceptions import TimesFMDatasetError
from src.contracts.schemas.timesfm_multifactor import TimesFMPrepareRequest
from src.repositories.market_bar import MarketBarRepository
from src.repositories.reference_quote import ReferenceQuoteRepository
from src.runtime.dependencies import RuntimeDependencies
from src.services.decision.cost_calculator import CostCalculator


def _runtime(tmp_path: Path) -> RuntimeDependencies:
    return RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            timesfm_enabled=False,
            news_enabled=False,
            live_quotes_enabled=False,
            market_bars_enabled=False,
            news_sync_enabled=False,
            jd_holdings_sync_enabled=False,
            jd_proactive_auth_enabled=False,
        )
    )


def _seed_quotes(runtime: RuntimeDependencies, count: int = 80, *, tail_price: float | None = None) -> datetime:
    """在连续交易周（周一 09:00 北京时间起，即 UTC 01:00）写入小时双边报价。"""
    start = datetime(2026, 3, 2, 1, 0, tzinfo=UTC)
    for i in range(count):
        ts = start + timedelta(hours=i)
        mid = 620.0 + 5.0 * math.sin(i / 4.0) + 0.05 * i
        if tail_price is not None and i >= count - 5:
            mid = tail_price
        runtime.quotes.save_quote(
            QuoteRaw(
                quote_id=f"q_tfm_{i:04d}",
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
    return start


def test_prepare_from_quotes_and_strict_pit_causality(tmp_path: Path) -> None:
    rt_a = _runtime(tmp_path / "a")
    rt_b = _runtime(tmp_path / "b")
    _seed_quotes(rt_a, count=72, tail_price=None)
    _seed_quotes(rt_b, count=72, tail_price=9999.0)

    builder_a = rt_a.timesfm_pipeline._builder
    builder_b = rt_b.timesfm_pipeline._builder

    req = TimesFMPrepareRequest(
        source_mode="quotes",
        product_code="GOLD_ACCUMULATION",
        freq="1h",
        factor_preset="gold_multifactor",
        context_length=32,
        stride=1,
        horizons=[HoldingPeriod.H1, HoldingPeriod.D1],
    )
    rep_a = builder_a.prepare_dataset(req, target_dir=tmp_path / "ds_a")
    rep_b = builder_b.prepare_dataset(req, target_dir=tmp_path / "ds_b")

    assert rep_a.written_files == 2
    assert rep_b.written_files == 2
    assert rep_a.manifest.past_only_factors == list(builder_a.PAST_ONLY_BASE)
    assert rep_a.manifest.past_future_factors == list(builder_a.FUTURE_FACTORS)
    assert rep_a.manifest.tabular_factors == list(builder_a.TABULAR_FACTORS)

    manifest_a, arr_a = builder_a.load_dataset(tmp_path / "ds_a")
    _, arr_b = builder_b.load_dataset(tmp_path / "ds_b")

    assert arr_a["past_only"].shape == (10, manifest_a.total_bars)
    assert arr_a["tabular"].shape == (manifest_a.total_bars, 12)
    assert np.all(np.isfinite(arr_a["past_only"]))

    # 篡改最后 5 小时报价不应影响此前任意时刻的上下文与特征（严格 PIT 因果性）
    check_idx = 40
    ctx_a, po_a, pf_a = builder_a.build_window(arr_a, manifest_a, check_idx, 24, use_covariates=True)
    ctx_b, po_b, pf_b = builder_b.build_window(arr_b, manifest_a, check_idx, 24, use_covariates=True)
    assert po_a is not None
    assert pf_a is not None
    assert po_b is not None
    assert pf_b is not None
    assert ctx_a.shape == (32,)
    assert po_a.shape == (10, 32)
    assert pf_a.shape == (6, 32 + 24)
    np.testing.assert_allclose(ctx_a, ctx_b)
    np.testing.assert_allclose(po_a, po_b)
    np.testing.assert_allclose(pf_a, pf_b)
    np.testing.assert_allclose(arr_a["tabular"][: check_idx + 1], arr_b["tabular"][: check_idx + 1], equal_nan=True)

    # 扣费净收益标签必须与 CostCalculator 精确一致
    calc = CostCalculator(FeeRule(buy_fee_rate=rt_a.settings.buy_fee_rate, sell_fee_rate=rt_a.settings.sell_fee_rate))
    i0 = int(arr_a["idx_1d"][0])
    expected_ret = calc.net_return(entry_ask=float(arr_a["ask"][i0]), exit_bid=float(arr_a["bid"][i0 + 24]))
    assert arr_a["label_1d"][0] == pytest.approx(expected_ret)


def test_prepare_from_reference_and_reject_overwrite(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    repo = ReferenceQuoteRepository(tmp_path / "references", database_path=rt.settings.database_path)
    base = datetime(2026, 3, 2, 1, 0, tzinfo=UTC)
    for i in range(60):
        ts = base + timedelta(hours=i)
        repo.save(
            ReferenceQuote(
                quote_id=f"ref_{i:04d}",
                product_code="CMBC-JCJ",
                price=round(720.0 + 3.0 * math.sin(i / 3.0), 4),
                unit="CNY/g",
                quote_at=ts,
                received_at=ts,
                available_at=ts,
                payload_hash=f"{i + 1:064x}",
            )
        )
    req = TimesFMPrepareRequest(
        source_mode="reference",
        product_code="CMBC-JCJ",
        freq="1h",
        context_length=32,
        horizons=[HoldingPeriod.H1],
        output_dir="ds_ref",
    )
    rep = rt.timesfm_pipeline.prepare_dataset(req)
    assert rep.manifest.source_mode == "reference"
    assert rep.manifest.sample_counts["1h"] >= 20
    with pytest.raises(TimesFMDatasetError, match="已存在"):
        rt.timesfm_pipeline.prepare_dataset(req)


def test_prepare_from_kline_xauusd_ohlcv_and_skip_unclosed_bars(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    bar_repo = MarketBarRepository(rt.settings.database_path)
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    bars: list[KLineBar] = []
    for i in range(60):
        bar_time = start + timedelta(hours=i)
        # XauusdBarMerge 读取时会自动过滤纽约维护小时（17:00 America/New_York）
        price = 2650.0 + 10.0 * math.sin(i / 5.0)
        bars.append(
            KLineBar(
                product_code=XAUUSD_PRODUCT,
                interval="m60",
                source="dukascopy_bid",
                bar_time=bar_time,
                trade_date=bar_time.date().isoformat(),
                open_price=round(price - 1.0, 2),
                high_price=round(price + 2.0, 2),
                low_price=round(price - 2.0, 2),
                close_price=round(price, 2),
                volume=100.0 + i,
                collected_at=bar_time + timedelta(hours=1),
                available_at=bar_time + timedelta(hours=1),
            )
        )
    # 注入一条未来尚未收盘的 bar，必须被严格排除
    future_time = datetime.now(UTC) + timedelta(hours=2)
    bars.append(
        KLineBar(
            product_code=XAUUSD_PRODUCT,
            interval="m60",
            source="dukascopy_bid",
            bar_time=future_time,
            trade_date=future_time.date().isoformat(),
            open_price=9999.0,
            high_price=9999.0,
            low_price=9999.0,
            close_price=9999.0,
            volume=1.0,
            collected_at=future_time,
            available_at=future_time + timedelta(hours=1),
        )
    )
    bar_repo.save_klines(bars)

    req = TimesFMPrepareRequest(
        source_mode="kline",
        product_code=XAUUSD_PRODUCT,
        freq="1h",
        factor_preset="gold_ohlcv_multifactor",
        context_length=32,
        horizons=[HoldingPeriod.H1],
        output_dir="ds_kline",
    )
    rep = rt.timesfm_pipeline.prepare_dataset(req)
    assert rep.manifest.past_only_factors[-3:] == ["hl_range_pct", "body_pct", "volume_log"]
    _, arrays = rt.timesfm_pipeline._builder.load_dataset(Path(rep.manifest.dataset_dir))
    assert arrays["past_only"].shape[0] == 13
    assert float(np.max(arrays["close"])) < 5000.0


def test_future_calendar_skips_weekend_and_validates_kline_interval_and_cache(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    builder = rt.timesfm_pipeline._builder

    # 周五 18:00 UTC（北京时间周六 02:00）往后推 2 步 1h 应跨过周末休市跳至周一开市
    friday_late = datetime(2026, 3, 6, 18, 0, tzinfo=UTC)
    fut = builder.future_calendar(friday_late.timestamp(), "1h", 2)
    assert fut.shape == (6, 2)
    # 跳过周末后第 1 步落在周一开市，不再处于周末风险窗且距离收盘大于 100 小时
    assert float(fut[5, 0]) == 0.0
    assert float(fut[4, 0]) > 0.8

    # _calendar_cache 必须受容量上限约束
    builder._calendar_cache.clear()
    for step in range(builder._CALENDAR_CACHE_SIZE + 20):
        builder._calendar_tuple(friday_late.timestamp() + step * 60.0)
    assert len(builder._calendar_cache) == builder._CALENDAR_CACHE_SIZE

    # freq 与 kline_interval 不一致必须拒绝
    with pytest.raises(TimesFMDatasetError, match="不一致"):
        builder.prepare_dataset(
            TimesFMPrepareRequest(
                source_mode="kline",
                product_code="CMBC-JCJ",
                freq="1h",
                kline_interval="day",
                context_length=32,
                horizons=[HoldingPeriod.H1],
            ),
            target_dir=tmp_path / "bad_interval",
        )
