"""特征工程计算引擎服务。

整合价格动量、已实现波动率、银行报价微结构、基差、交易日历以及时序与新闻事件特征。
"""

import bisect
import math
import statistics
from datetime import UTC, datetime

from src.agents.timesfm import TimesFMAgent
from src.contracts.domains.feature import FeatureSet
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState, HoldingPeriod
from src.services.market.session_calendar import SessionCalendar
from src.services.risk.news_risk import NewsRiskEngine


class ModelContextUnavailableError(RuntimeError):
    """真实时序模型尚无足够上下文，回放应预热并弃权。"""


class FeatureEngine:
    """特征工程统一提取服务。"""

    def __init__(
        self,
        calendar: SessionCalendar | None = None,
        timesfm_agent: TimesFMAgent | None = None,
        news_engine: NewsRiskEngine | None = None,
        *,
        timesfm_adapter: TimesFMAgent | None = None,
    ) -> None:
        self._calendar = calendar or SessionCalendar()
        self._timesfm = timesfm_agent or timesfm_adapter or TimesFMAgent()
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
        intervals: list[float] = []
        for i in range(1, len(quotes)):
            p0 = quotes[i - 1].bid_price
            p1 = quotes[i].bid_price
            interval = (quotes[i].quote_at - quotes[i - 1].quote_at).total_seconds()
            if p0 > 0 and interval > 0:
                returns.append((p1 - p0) / p0)
                intervals.append(interval)
        if len(returns) < 2:
            return 0.0
        std = statistics.stdev(returns)
        # 不等间隔以有效平均采样间隔近似年化，闭市时间计入实际经过时间。
        annual_periods = 365.0 * 86400.0 / statistics.mean(intervals)
        annualized_vol = std * math.sqrt(annual_periods)
        return round(annualized_vol, 6)

    def extract_features(
        self,
        history_quotes: list[QuoteRaw],
        intl_ref_cny: float | None = None,
        as_of: datetime | None = None,
        horizon: HoldingPeriod = HoldingPeriod.W1,
    ) -> FeatureSet:
        """从历史报价序列与当前环境状态中提取全量特征向量。"""
        history_quotes = sorted(
            (
                q
                for q in history_quotes
                if q.quality_state == DataQualityState.NORMAL
                and math.isfinite(q.bid_price)
                and math.isfinite(q.ask_price)
                and 0 < q.bid_price <= q.ask_price
                and (as_of is None or max(q.quote_at, q.received_at, q.available_at or q.received_at) <= as_of)
            ),
            key=lambda q: q.quote_at,
        )
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
        # 缺少 OHLC，保留近二十次价格标准差作为 ATR 代理。
        prices = [q.bid_price for q in history_quotes[-20:]]
        atr = round(statistics.stdev(prices), 4) if len(prices) >= 2 else 0.0

        # 日历特征（统一以有效决策时点 ref_dt 为准）
        ref_dt = as_of or latest_q.quote_at
        hours_to_close = self._calendar.get_hours_until_weekend_close(ref_dt)
        is_risk_window = self._calendar.is_in_weekend_risk_window(ref_dt)
        hours_since_open = (
            max(self._calendar.trading_hours_per_week() - hours_to_close, 0.0)
            if self._calendar.is_trading_time(ref_dt)
            else 0.0
        )

        # 时序模型特征（优先使用原子返回状态码避免并发竞态）
        if hasattr(self._timesfm, "predict_feature_with_status"):
            timesfm_return, error_code = self._timesfm.predict_feature_with_status(
                history_quotes, horizon=horizon, as_of=ref_dt
            )
            is_enabled = bool(self._timesfm.get_model_info()["is_enabled"])
        else:
            timesfm_return = self._timesfm.predict_feature(history_quotes, horizon=horizon, as_of=ref_dt)
            model_info = self._timesfm.get_model_info()
            is_enabled = bool(model_info["is_enabled"])
            error_code = str(model_info.get("error_code")) if model_info.get("error_code") else None
        if is_enabled and timesfm_return is None:
            if error_code == "insufficient_context":
                raise ModelContextUnavailableError("TimesFM 连续小时上下文不足")
            raise RuntimeError("已启用的 TimesFM 未生成有效特征，请检查模型状态与连续小时行情")

        # 新闻风险特征
        active_flags = self._news_engine.get_active_risk_flags(ref_dt)

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

    def create_stream(self) -> "IncrementalFeatureStream":
        """创建增量特征提取会话，用于回测流式回放时复用公共特征与在线波动率统计。"""
        return IncrementalFeatureStream(self)


class IncrementalFeatureStream:
    """流式增量特征提取器，将回测每步公共统计从 O(n) 降至 O(log n) / O(1)。"""

    def __init__(self, engine: FeatureEngine) -> None:
        self._engine = engine
        self._quotes: list[QuoteRaw] = []
        self._quote_ts: list[float] = []
        self._vol_count: int = 0
        self._vol_mean: float = 0.0
        self._vol_m2: float = 0.0
        self._vol_interval_sum: float = 0.0
        self._vol_dirty: bool = False

    def _add_return_sample(self, prev_q: QuoteRaw, curr_q: QuoteRaw) -> None:
        p0 = prev_q.bid_price
        p1 = curr_q.bid_price
        interval = (curr_q.quote_at - prev_q.quote_at).total_seconds()
        if p0 > 0 and interval > 0:
            ret = (p1 - p0) / p0
            self._vol_count += 1
            delta = ret - self._vol_mean
            self._vol_mean += delta / self._vol_count
            self._vol_m2 += delta * (ret - self._vol_mean)
            self._vol_interval_sum += interval

    def _rebuild_vol_stats(self) -> None:
        self._vol_count = 0
        self._vol_mean = 0.0
        self._vol_m2 = 0.0
        self._vol_interval_sum = 0.0
        for idx in range(1, len(self._quotes)):
            self._add_return_sample(self._quotes[idx - 1], self._quotes[idx])
        self._vol_dirty = False

    def _current_realized_volatility(self) -> float:
        if self._vol_dirty:
            self._rebuild_vol_stats()
        if self._vol_count < 2 or self._vol_interval_sum <= 0:
            return 0.0
        variance = max(self._vol_m2 / (self._vol_count - 1), 0.0)
        std = math.sqrt(variance)
        mean_interval = self._vol_interval_sum / self._vol_count
        annual_periods = 365.0 * 86400.0 / mean_interval
        return round(std * math.sqrt(annual_periods), 6)

    def _calc_return_bisect(self, current_price: float, target_hours: float) -> float | None:
        if not self._quotes or current_price <= 0:
            return None
        target_ts = self._quote_ts[-1] - target_hours * 3600.0
        idx = bisect.bisect_right(self._quote_ts, target_ts) - 1
        if idx < 0:
            return None
        past_price = self._quotes[idx].bid_price
        if past_price > 0:
            return round((current_price - past_price) / past_price, 6)
        return None

    def _predict_timesfm(self, horizon: HoldingPeriod, ref_dt: datetime) -> float | None:
        timesfm = self._engine._timesfm
        if hasattr(timesfm, "predict_feature_with_status"):
            timesfm_return, error_code = timesfm.predict_feature_with_status(
                self._quotes, horizon=horizon, as_of=ref_dt
            )
            is_enabled = bool(timesfm.get_model_info()["is_enabled"])
        else:
            timesfm_return = timesfm.predict_feature(self._quotes, horizon=horizon, as_of=ref_dt)
            model_info = timesfm.get_model_info()
            is_enabled = bool(model_info["is_enabled"])
            error_code = str(model_info.get("error_code")) if model_info.get("error_code") else None
        if is_enabled and timesfm_return is None:
            if error_code == "insufficient_context":
                raise ModelContextUnavailableError("TimesFM 连续小时上下文不足")
            raise RuntimeError("已启用的 TimesFM 未生成有效特征，请检查模型状态与连续小时行情")
        return timesfm_return

    def push_and_extract(
        self,
        quote: QuoteRaw,
        *,
        as_of: datetime,
        horizons: tuple[HoldingPeriod, ...],
        intl_ref_cny: float | None = None,
    ) -> dict[HoldingPeriod, FeatureSet]:
        """追加单条按 PIT 到达的报价，并为多个持有期一次性提取共享特征向量。"""
        is_valid = (
            quote.quality_state == DataQualityState.NORMAL
            and math.isfinite(quote.bid_price)
            and math.isfinite(quote.ask_price)
            and 0 < quote.bid_price <= quote.ask_price
            and max(quote.quote_at, quote.received_at, quote.available_at or quote.received_at) <= as_of
        )
        if is_valid:
            ts = quote.quote_at.timestamp()
            if not self._quote_ts or ts >= self._quote_ts[-1]:
                if self._quotes and not self._vol_dirty:
                    self._add_return_sample(self._quotes[-1], quote)
                self._quotes.append(quote)
                self._quote_ts.append(ts)
            else:
                pos = bisect.bisect_right(self._quote_ts, ts)
                self._quotes.insert(pos, quote)
                self._quote_ts.insert(pos, ts)
                self._vol_dirty = True

        if not self._quotes:
            empty = FeatureSet(quote_id="empty", as_of=as_of)
            return dict.fromkeys(horizons, empty)

        latest_q = self._quotes[-1]
        mid = (latest_q.bid_price + latest_q.ask_price) / 2.0
        spread = round(latest_q.ask_price - latest_q.bid_price, 4)
        spread_pct = round(spread / mid, 6) if mid > 0 else 0.0
        basis = round((mid / intl_ref_cny) - 1.0, 6) if intl_ref_cny is not None and intl_ref_cny > 0 else None

        ret_1d = self._calc_return_bisect(latest_q.bid_price, target_hours=24.0)
        ret_3d = self._calc_return_bisect(latest_q.bid_price, target_hours=72.0)
        ret_1w = self._calc_return_bisect(latest_q.bid_price, target_hours=168.0)

        r_vol = self._current_realized_volatility()
        prices = [q.bid_price for q in self._quotes[-20:]]
        atr = round(statistics.stdev(prices), 4) if len(prices) >= 2 else 0.0

        hours_to_close = self._engine._calendar.get_hours_until_weekend_close(as_of)
        is_risk_window = self._engine._calendar.is_in_weekend_risk_window(as_of)
        hours_since_open = (
            max(self._engine._calendar.trading_hours_per_week() - hours_to_close, 0.0)
            if self._engine._calendar.is_trading_time(as_of)
            else 0.0
        )
        active_flags = self._engine._news_engine.get_active_risk_flags(as_of)

        base = FeatureSet(
            quote_id=latest_q.quote_id,
            as_of=as_of,
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
            timesfm_forecast_return=None,
            event_risk_flag_count=len(active_flags),
        )

        is_timesfm_enabled = bool(self._engine._timesfm.get_model_info()["is_enabled"])
        if not is_timesfm_enabled:
            return dict.fromkeys(horizons, base)

        result: dict[HoldingPeriod, FeatureSet] = {}
        for horizon in horizons:
            tf_ret = self._predict_timesfm(horizon, as_of)
            result[horizon] = base.model_copy(update={"timesfm_forecast_return": tf_ret})
        return result
