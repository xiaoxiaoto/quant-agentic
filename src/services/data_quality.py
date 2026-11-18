"""数据质量门禁服务。

校验规则项：
1. 负价格或买卖价倒挂（ask < bid）。
2. 未来时间戳与时钟偏移偏差。
3. 乱序到达或重复报价。
4. 价格剧烈异常跳变。
5. 点差异常扩张。
6. 行情断流与陈旧状态判定。
"""

from datetime import UTC, datetime

from src.types.domain.quote import QuoteRaw
from src.types.enums.trading import DataQualityState


class DataQualityGuard:
    """实时报价质量门禁与状态跟踪器。"""

    def __init__(
        self,
        max_clock_skew_seconds: float = 5.0,
        max_price_jump_pct: float = 0.03,
        max_spread_pct: float = 0.02,
        stale_threshold_seconds: float = 30.0,
    ) -> None:
        self._max_clock_skew_seconds = max_clock_skew_seconds
        self._max_price_jump_pct = max_price_jump_pct
        self._max_spread_pct = max_spread_pct
        self._stale_threshold_seconds = stale_threshold_seconds
        self._seen_quote_ids: set[str] = set()
        self._last_quote: QuoteRaw | None = None

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def validate_and_classify(
        self,
        quote: QuoteRaw,
        now: datetime | None = None,
    ) -> DataQualityState:
        """对单条新报价进行质量校验并分类。"""
        current_time = now or self._now()

        # 1. 价格非正或点差倒挂检查
        if quote.bid_price <= 0 or quote.ask_price <= 0:
            return DataQualityState.ANOMALY
        if quote.ask_price < quote.bid_price:
            return DataQualityState.ANOMALY

        # 2. 时钟未来偏移校验
        recv = quote.received_at
        if (quote.quote_at - recv).total_seconds() > self._max_clock_skew_seconds:
            return DataQualityState.ANOMALY

        # 3. 唯一报价 ID 重复校验
        if quote.quote_id in self._seen_quote_ids:
            return DataQualityState.ANOMALY

        # 4. 时间戳倒流（乱序）检查
        if self._last_quote is not None:
            if quote.quote_at < self._last_quote.quote_at:
                return DataQualityState.ANOMALY

            # 5. 中间价异常跳价检查
            last_mid = (self._last_quote.bid_price + self._last_quote.ask_price) / 2.0
            cur_mid = (quote.bid_price + quote.ask_price) / 2.0
            if last_mid > 0:
                jump = abs(cur_mid / last_mid - 1.0)
                if jump > self._max_price_jump_pct:
                    return DataQualityState.ANOMALY

        # 6. 点差率异常扩张检查
        mid = (quote.bid_price + quote.ask_price) / 2.0
        if mid > 0:
            spread_pct = (quote.ask_price - quote.bid_price) / mid
            if spread_pct > self._max_spread_pct:
                return DataQualityState.ANOMALY

        # 7. 报价陈旧检测（相对当前系统时钟）
        quote_age = (current_time - min(quote.quote_at, quote.received_at)).total_seconds()
        if quote_age > self._stale_threshold_seconds:
            return DataQualityState.STALE

        # 校验正常则更新状态追踪
        self._seen_quote_ids.add(quote.quote_id)
        self._last_quote = quote
        return DataQualityState.NORMAL

    def is_stale(
        self,
        quote: QuoteRaw,
        current_time: datetime | None = None,
    ) -> bool:
        """检查已有有效报价在当前时刻是否已超时陈旧。"""
        now = current_time or self._now()
        age = (now - min(quote.quote_at, quote.received_at)).total_seconds()
        return age > self._stale_threshold_seconds

    def reset(self) -> None:
        """清空门禁历史状态（用于测试隔离或服务重启）。"""
        self._seen_quote_ids.clear()
        self._last_quote = None
