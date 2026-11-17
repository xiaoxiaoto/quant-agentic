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
