"""采集器数据质量与周末跳空统计分析服务。

分析原始行情的采样时延、数据缺口、点差统计分布以及周末闭市/开盘价格跳空幅度。
"""

import statistics
import uuid
from datetime import UTC, datetime

from src.services.session_calendar import SessionCalendar
from src.types.domain.quality import CollectorQualityReport, WeekendGapRecord
from src.types.domain.quote import QuoteRaw


class CollectorQualityAnalyzer:
    """采集器数据质量与行情统计分析引擎。"""

    def __init__(
        self,
        expected_interval_seconds: float = 60.0,
        calendar: SessionCalendar | None = None,
    ) -> None:
        self._interval_s = expected_interval_seconds
        self._calendar = calendar or SessionCalendar()

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _percentile(self, values: list[float], p: float) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        k = (len(sorted_vals) - 1) * p
        f = int(k)
        c = min(f + 1, len(sorted_vals) - 1)
        d = k - f
        return round(sorted_vals[f] * (1.0 - d) + sorted_vals[c] * d, 4)

    def analyze(self, quotes: list[QuoteRaw]) -> CollectorQualityReport:
        """全量分析行情集合的数据完整性、时延、点差及周末跳空风险。"""
        if not quotes:
            now_dt = self._now()
            return CollectorQualityReport(
                report_id=f"qual_{uuid.uuid4().hex[:8]}",
                start_time=now_dt,
                end_time=now_dt,
                total_records=0,
            )

        sorted_quotes = sorted(quotes, key=lambda q: q.quote_at)
        start_time = sorted_quotes[0].quote_at
        end_time = sorted_quotes[-1].quote_at

        # 1. 延迟分析
        latencies_ms: list[float] = []
        for q in sorted_quotes:
            lat = max((q.received_at - q.quote_at).total_seconds() * 1000.0, 0.0)
            latencies_ms.append(lat)

        avg_lat = statistics.mean(latencies_ms) if latencies_ms else 0.0
        p95_lat = self._percentile(latencies_ms, 0.95)

        # 2. 点差分析
        spreads = [round(q.ask_price - q.bid_price, 4) for q in sorted_quotes]
        spread_mean = statistics.mean(spreads) if spreads else 0.0
        spread_median = statistics.median(spreads) if spreads else 0.0
        spread_p95 = self._percentile(spreads, 0.95)
        spread_p99 = self._percentile(spreads, 0.99)

        # 3. 交易时段内数据缺口检测
        gap_count = 0
        max_gap_s = 0.0
        gap_threshold = self._interval_s * 3.0

        for i in range(1, len(sorted_quotes)):
            prev_q = sorted_quotes[i - 1]
            curr_q = sorted_quotes[i]
            diff_s = (curr_q.quote_at - prev_q.quote_at).total_seconds()

            # 仅在两端均处于开市交易时段内时判定为异常缺口
            if (
                self._calendar.is_trading_time(prev_q.quote_at)
                and self._calendar.is_trading_time(curr_q.quote_at)
                and diff_s > gap_threshold
            ):
                gap_count += 1
                if diff_s > max_gap_s:
                    max_gap_s = diff_s

        # 4. 周末跳空统计 (周六凌晨收盘 vs 周一上午开盘)
        weekend_gaps: list[WeekendGapRecord] = []
        for i in range(1, len(sorted_quotes)):
            prev_q = sorted_quotes[i - 1]
            curr_q = sorted_quotes[i]

            # 判断是否跨越了周末休市
            time_gap_hours = (curr_q.quote_at - prev_q.quote_at).total_seconds() / 3600.0
            if time_gap_hours >= 48.0:
                prev_mid = (prev_q.bid_price + prev_q.ask_price) / 2.0
                curr_mid = (curr_q.bid_price + curr_q.ask_price) / 2.0
                gap_amt = round(curr_mid - prev_mid, 4)
                gap_pct = round(gap_amt / prev_mid, 6) if prev_mid > 0 else 0.0

                weekend_gaps.append(
                    WeekendGapRecord(
                        close_time=prev_q.quote_at,
                        open_time=curr_q.quote_at,
                        close_mid_price=prev_mid,
                        open_mid_price=curr_mid,
                        gap_amount=gap_amt,
                        gap_pct=gap_pct,
                    )
                )

        max_w_gap = max([abs(g.gap_pct) for g in weekend_gaps], default=0.0)
        mean_w_gap = statistics.mean([abs(g.gap_pct) for g in weekend_gaps]) if weekend_gaps else 0.0

        return CollectorQualityReport(
            report_id=f"qual_{uuid.uuid4().hex[:8]}",
            start_time=start_time,
            end_time=end_time,
            total_records=len(sorted_quotes),
            gap_count=gap_count,
            max_gap_seconds=round(max_gap_s, 2),
            avg_latency_ms=round(avg_lat, 2),
            p95_latency_ms=round(p95_lat, 2),
            spread_mean=round(spread_mean, 4),
            spread_median=round(spread_median, 4),
            spread_p95=spread_p95,
            spread_p99=spread_p99,
            weekend_gaps=weekend_gaps,
            max_weekend_gap_pct=round(max_w_gap, 6),
            mean_weekend_gap_pct=round(mean_w_gap, 6),
        )
