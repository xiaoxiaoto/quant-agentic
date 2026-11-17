"""基础时序模型适配器单元测试。"""

from datetime import UTC, datetime, timedelta

from src.services.timesfm_adapter import TimesFMAdapter
from src.types.domain.quote import QuoteRaw
from src.types.enums.trading import HoldingPeriod


def test_timesfm_adapter_disabled_fallback() -> None:
    adapter = TimesFMAdapter(is_enabled=False)
    assert not adapter.is_available()

    quotes = [
        QuoteRaw(
            quote_id=f"q_{i}",
            source_id="src",
            bid_price=600.0 + i,
            ask_price=600.5 + i,
            quote_at=datetime.now(UTC),
            received_at=datetime.now(UTC),
            is_tradable=True,
        )
        for i in range(10)
    ]
    feat = adapter.predict_feature(quotes, horizon=HoldingPeriod.W1)
    assert feat is None


def test_timesfm_adapter_prediction_output() -> None:
    adapter = TimesFMAdapter(is_enabled=True)
    assert adapter.is_available()
    base_t = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)

    # 构造持续上涨行情
    quotes = [
        QuoteRaw(
            quote_id=f"q_{i}",
            source_id="src",
            bid_price=600.0 + i * 2.0,
            ask_price=600.5 + i * 2.0,
            quote_at=base_t + timedelta(hours=i),
            received_at=base_t + timedelta(hours=i),
            is_tradable=True,
        )
        for i in range(10)
    ]

    feat = adapter.predict_feature(quotes, horizon=HoldingPeriod.W1)
    assert feat is not None
    assert feat > 0.0  # 上涨动量得到正向收益特征
