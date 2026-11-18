"""特征工程提取单元测试。"""

from datetime import UTC, datetime, timedelta

from src.services.feature_engine import FeatureEngine
from src.types.domain.quote import QuoteRaw


def test_feature_engine_empty() -> None:
    engine = FeatureEngine()
    feats = engine.extract_features([])
    assert feats.quote_id == "empty"
    assert feats.return_1d is None


def test_feature_engine_extraction() -> None:
    engine = FeatureEngine()
    base_t = datetime(2026, 3, 2, 9, 30, tzinfo=UTC)

    # 构造跨越数天的报价序列（19 * 5 = 95 小时，落于周五交易时段）
    quotes = [
        QuoteRaw(
            quote_id=f"q_{i}",
            source_id="src",
            bid_price=600.0 + i * 1.5,
            ask_price=600.6 + i * 1.5,  # spread = 0.6
            quote_at=base_t + timedelta(hours=i * 5),
            received_at=base_t + timedelta(hours=i * 5),
            is_tradable=True,
        )
        for i in range(20)
    ]

    feats = engine.extract_features(quotes, intl_ref_cny=605.0)
    assert feats.quote_id == "q_19"
    assert feats.spread == 0.6
    assert feats.spread_pct > 0.0
    assert feats.return_1d is not None
    assert feats.return_3d is not None
    assert feats.return_1d > 0.0  # 连续上涨
    assert feats.bank_basis is not None
    assert feats.hours_to_weekend_close > 0.0


def test_features_respect_explicit_pit_cutoff() -> None:
    now = datetime(2026, 3, 2, tzinfo=UTC)
    quote = QuoteRaw(
        quote_id="visible",
        source_id="s",
        bid_price=600,
        ask_price=601,
        quote_at=now,
        received_at=now,
        available_at=now,
        is_tradable=True,
    )
    delayed = quote.model_copy(update={"quote_id": "delayed", "available_at": now + timedelta(hours=1)})
    future = quote.model_copy(
        update={"quote_id": "future", "quote_at": now + timedelta(days=1), "received_at": now + timedelta(days=1)}
    )
    result = FeatureEngine().extract_features([quote, future, delayed], as_of=now)
    assert result.quote_id == "visible"
    assert result.return_1d is None


def test_volatility_annualization_uses_observed_interval() -> None:
    import math

    import pytest

    now = datetime(2026, 3, 2, tzinfo=UTC)
    quotes = [
        QuoteRaw(
            quote_id=str(i),
            source_id="s",
            bid_price=price,
            ask_price=price,
            quote_at=now + timedelta(hours=i),
            received_at=now + timedelta(hours=i),
            is_tradable=True,
        )
        for i, price in enumerate((600, 606, 603, 612))
    ]
    daily = [q.model_copy(update={"quote_at": now + timedelta(days=i)}) for i, q in enumerate(quotes)]
    engine = FeatureEngine()
    hourly_vol = engine.extract_features(quotes).realized_volatility
    daily_vol = engine.extract_features(daily).realized_volatility
    assert hourly_vol / daily_vol == pytest.approx(math.sqrt(24), abs=1e-4)
