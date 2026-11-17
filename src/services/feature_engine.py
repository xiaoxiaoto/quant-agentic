"""特征工程计算引擎服务。

整合价格动量、已实现波动率、银行报价微结构、基差、交易日历以及时序与新闻事件特征。
"""

import math
import statistics
from datetime import UTC, datetime

from src.services.news_risk import NewsRiskEngine
from src.services.session_calendar import SessionCalendar
from src.services.timesfm_adapter import TimesFMAdapter
from src.types.domain.feature import FeatureSet
from src.types.domain.quote import QuoteRaw


class FeatureEngine:
    """特征工程统一提取服务。"""

    def __init__(
        self,
        calendar: SessionCalendar | None = None,
        timesfm_adapter: TimesFMAdapter | None = None,
        news_engine: NewsRiskEngine | None = None,
    ) -> None:
        self._calendar = calendar or SessionCalendar()
        self._timesfm = timesfm_adapter or TimesFMAdapter()
        self._news_engine = news_engine or NewsRiskEngine()

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _calc_historical_return(
        self,
        quotes: list[QuoteRaw],
        current_price: float,
        target_hours: float,
    ) -> float | None:
        if not quotes or current_price <= 0:
            return None
        curr_time = quotes[-1].quote_at
        # 寻找距离当前最接近 target_hours 前的报价
        best_past_q: QuoteRaw | None = None
        for q in reversed(quotes):
            diff_h = (curr_time - q.quote_at).total_seconds() / 3600.0
            if diff_h >= target_hours:
                best_past_q = q
                break
        if best_past_q and best_past_q.bid_price > 0:
            return round((current_price - best_past_q.bid_price) / best_past_q.bid_price, 6)
        return None

    def _calc_realized_volatility(self, quotes: list[QuoteRaw]) -> float:
        if len(quotes) < 3:
            return 0.0
        returns: list[float] = []
        for i in range(1, len(quotes)):
            p0 = quotes[i - 1].bid_price
            p1 = quotes[i].bid_price
            if p0 > 0:
                returns.append((p1 - p0) / p0)
        if len(returns) < 2:
            return 0.0
        std = statistics.stdev(returns)
        # 换算为年化波动率（假设每年约 250 个交易日）
        annualized_vol = std * math.sqrt(250.0 * 24.0)
        return round(annualized_vol, 6)

    def extract_features(
        self,
        history_quotes: list[QuoteRaw],
        intl_ref_cny: float | None = None,
        as_of: datetime | None = None,
    ) -> FeatureSet:
        """从历史报价序列与当前环境状态中提取全量特征向量。"""
        if not history_quotes:
            now_dt = as_of or self._now()
            return FeatureSet(
                quote_id="empty",
                as_of=now_dt,
            )

        latest_q = history_quotes[-1]
        mid = (latest_q.bid_price + latest_q.ask_price) / 2.0
        spread = round(latest_q.ask_price - latest_q.bid_price, 4)
        spread_pct = round(spread / mid, 6) if mid > 0 else 0.0

        # 基差计算
        basis: float | None = None
        if intl_ref_cny is not None and intl_ref_cny > 0:
            basis = round((mid / intl_ref_cny) - 1.0, 6)

        # 动量收益
        ret_1d = self._calc_historical_return(history_quotes, latest_q.bid_price, target_hours=24.0)
        ret_3d = self._calc_historical_return(history_quotes, latest_q.bid_price, target_hours=72.0)
        ret_1w = self._calc_historical_return(history_quotes, latest_q.bid_price, target_hours=168.0)

        # 波动率
        r_vol = self._calc_realized_volatility(history_quotes)
        # 近似 ATR
        prices = [q.bid_price for q in history_quotes[-20:]]
        atr = round(statistics.stdev(prices), 4) if len(prices) >= 2 else 0.0

        # 日历特征
        hours_to_close = self._calendar.get_hours_until_weekend_close(latest_q.quote_at)
        is_risk_window = self._calendar.is_in_weekend_risk_window(latest_q.quote_at)
        hours_since_open = max(113.5 - hours_to_close, 0.0)

        # 时序模型特征
        timesfm_return = self._timesfm.predict_feature(history_quotes)

        # 新闻风险特征
        active_flags = self._news_engine.get_active_risk_flags(latest_q.quote_at)

        return FeatureSet(
            quote_id=latest_q.quote_id,
            as_of=as_of or latest_q.quote_at,
            return_1d=ret_1d,
            return_3d=ret_3d,
            return_1w=ret_1w,
            realized_volatility=r_vol,
            atr=atr,
            spread=spread,
            spread_pct=spread_pct,
            bank_basis=basis,
            hours_to_weekend_close=round(hours_to_close, 2),
            hours_since_open=round(hours_since_open, 2),
            is_weekend_risk_window=is_risk_window,
            timesfm_forecast_return=timesfm_return,
            event_risk_flag_count=len(active_flags),
        )
