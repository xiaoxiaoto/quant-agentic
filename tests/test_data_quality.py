"""数据质量门禁单元测试。"""

from datetime import UTC, datetime, timedelta

from src.services.data_quality import DataQualityGuard
from src.types.domain.quote import QuoteRaw
from src.types.enums.trading import DataQualityState


def _make_quote(
    quote_id: str = "q1",
    bid: float = 650.0,
    ask: float = 650.5,
    quote_offset_sec: float = 0.0,
    recv_offset_sec: float = 0.0,
) -> tuple[QuoteRaw, datetime]:
    base = datetime(2026, 11, 17, 10, 0, 0, tzinfo=UTC)
    q_at = base + timedelta(seconds=quote_offset_sec)
    r_at = base + timedelta(seconds=recv_offset_sec)
    q = QuoteRaw(
        quote_id=quote_id,
        source_id="mock_source",
        bid_price=bid,
        ask_price=ask,
        quote_at=q_at,
        received_at=r_at,
        is_tradable=True,
    )
    return q, base


class TestDataQualityGuard:
    def test_valid_quote_passes(self) -> None:
        """合规双边报价正常通过校验。"""
        guard = DataQualityGuard()
        q, base = _make_quote()
        state = guard.validate_and_classify(q, now=base)
        assert state == DataQualityState.NORMAL

    def test_negative_or_zero_price_anomaly(self) -> None:
        """负价格或零价格应标记为异常（ANOMALY）。"""
        guard = DataQualityGuard()
        q, base = _make_quote(bid=-1.0, ask=650.0)
        assert guard.validate_and_classify(q, now=base) == DataQualityState.ANOMALY

        q2, _ = _make_quote(bid=0.0, ask=650.0)
        assert guard.validate_and_classify(q2, now=base) == DataQualityState.ANOMALY

    def test_inverted_spread_anomaly(self) -> None:
        """买卖价倒挂（ask < bid）应标记为异常。"""
        guard = DataQualityGuard()
        q, base = _make_quote(bid=651.0, ask=650.0)
        assert guard.validate_and_classify(q, now=base) == DataQualityState.ANOMALY

    def test_future_clock_skew_anomaly(self) -> None:
        """报价源时间戳严重超前于本地接收时间（未来时间）标记为异常。"""
        guard = DataQualityGuard(max_clock_skew_seconds=5.0)
        # 报价时间戳超前接收时间 10 秒
        q, base = _make_quote(quote_offset_sec=10.0, recv_offset_sec=0.0)
        assert guard.validate_and_classify(q, now=base) == DataQualityState.ANOMALY

    def test_duplicate_quote_id_anomaly(self) -> None:
        """重复的 quote_id 应被门禁拦截。"""
        guard = DataQualityGuard()
        q1, base = _make_quote(quote_id="dup1")
        assert guard.validate_and_classify(q1, now=base) == DataQualityState.NORMAL

        q2, _ = _make_quote(quote_id="dup1")
        assert guard.validate_and_classify(q2, now=base) == DataQualityState.ANOMALY

    def test_out_of_order_quote_anomaly(self) -> None:
        """乱序到达（时间倒流）的报价应被识别为异常。"""
        guard = DataQualityGuard()
        q1, base = _make_quote(quote_id="q1", quote_offset_sec=10.0, recv_offset_sec=10.0)
        assert guard.validate_and_classify(q1, now=base + timedelta(seconds=10)) == DataQualityState.NORMAL

        # q2 的报价时间倒流为 5 秒
        q2, _ = _make_quote(quote_id="q2", quote_offset_sec=5.0, recv_offset_sec=11.0)
        assert guard.validate_and_classify(q2, now=base + timedelta(seconds=11)) == DataQualityState.ANOMALY

    def test_excessive_price_jump_anomaly(self) -> None:
        """中间价瞬间跳跃幅度超出阈值（如 >3%）时判定为跳价异常。"""
        guard = DataQualityGuard(max_price_jump_pct=0.03)
        q1, base = _make_quote(quote_id="q1", bid=600.0, ask=601.0)
        guard.validate_and_classify(q1, now=base)

        # 瞬间暴涨 10%
        q2, _ = _make_quote(quote_id="q2", bid=665.0, ask=666.0, quote_offset_sec=1.0, recv_offset_sec=1.0)
        assert guard.validate_and_classify(q2, now=base + timedelta(seconds=1)) == DataQualityState.ANOMALY

    def test_excessive_spread_anomaly(self) -> None:
        """点差占中间价比率严重异常（如 >2%）时判定为异常。"""
        guard = DataQualityGuard(max_spread_pct=0.02)
        # 600 元中间价下点差高达 30 元（5%）
        q, base = _make_quote(bid=585.0, ask=615.0)
        assert guard.validate_and_classify(q, now=base) == DataQualityState.ANOMALY

    def test_stale_detection(self) -> None:
        """超期未更新的报价应标记为 STALE（陈旧）。"""
        guard = DataQualityGuard(stale_threshold_seconds=30.0)
        q, base = _make_quote()
        # 35 秒后校验
        now = base + timedelta(seconds=35)
        assert guard.validate_and_classify(q, now=now) == DataQualityState.STALE
        assert guard.is_stale(q, current_time=now) is True
