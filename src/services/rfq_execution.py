"""RFQ（报价接受）成交模型服务。

用于回测仿真与影子运行，替代交易所撮合模式：
成交价 = 决策后 eligible_at 起的最早可成交报价 + 延迟情景下的报价漂移 + 校准滑点
成交概率 = f(报价有效期, 点差状态, 波动状态, 临近开盘/事件)
"""

import uuid
from datetime import UTC, datetime
from math import isfinite

from src.types.domain.execution import RFQFillResult, RFQRequest
from src.types.domain.product import FeeRule
from src.types.domain.quote import QuoteRaw
from src.types.enums.trading import DataQualityState, TradeDirection


class RFQExecutionEngine:
    """RFQ（报价接受）成交仿真与撮合模型引擎。"""

    def __init__(
        self,
        fee_rule: FeeRule | None = None,
        max_quote_age_seconds: float = 60.0,
        manual_delay_drift_rate: float = 0.0002,
        prog_delay_drift_rate: float = 0.00005,
    ) -> None:
        self._fee_rule = fee_rule or FeeRule()
        self._max_quote_age_s = max_quote_age_seconds
        self._manual_drift = manual_delay_drift_rate
        self._prog_drift = prog_delay_drift_rate

    def _now(self) -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _visible_at(quote: QuoteRaw) -> datetime:
        return max(quote.quote_at, quote.received_at, quote.available_at or quote.received_at)

    def _compute_fill_probability(
        self,
        quote: QuoteRaw,
        request: RFQRequest,
        spread_pct: float,
    ) -> float:
        """评估本次请求在当前市况下的接受成交概率。"""
        # 1. 基础成交率（程序化更高，人工稍低）
        base_prob = 0.98 if request.is_programmatic else 0.92

        # 2. 报价时效惩罚（quote age 过大容易触发拒单或重新报价）
        quote_age = max((self._visible_at(quote) - quote.quote_at).total_seconds(), 0.0)
        if quote_age > self._max_quote_age_s:
            base_prob -= 0.35

        # 3. 点差异常扩张惩罚（高波动下拒单率上升）
        if spread_pct > 0.002:  # 点差大于 20bp
            base_prob -= 0.15

        return max(min(base_prob, 1.0), 0.0)

    def _estimate_slippage(
        self,
        base_price: float,
        is_programmatic: bool,
    ) -> float:
        """根据执行路径情景估计延迟漂移与滑点偏差。"""
        drift_rate = self._prog_drift if is_programmatic else self._manual_drift
        slippage = base_price * drift_rate
        return round(slippage, 4)

    def simulate_execution(
        self,
        request: RFQRequest,
        candidate_quotes: list[QuoteRaw],
    ) -> RFQFillResult:
        """在报价流中撮合执行 RFQ 请求。

        寻找 eligible_at 起的首条可成交报价并完成执行。
        """
        if not isfinite(request.grams) or request.grams <= 0:
            raise ValueError("请求克重必须为正且有限")
        if request.eligible_at < request.decision_time:
            raise ValueError("可成交时间不得早于决策时间")
        fill_id = f"fill_{uuid.uuid4().hex[:10]}"

        # 1. 寻找 eligible_at 之后的最早有效报价
        eligible_quotes = sorted(
            (
                q
                for q in candidate_quotes
                if q.is_tradable
                and q.quality_state == DataQualityState.NORMAL
                and self._visible_at(q) >= request.eligible_at
                and (self._visible_at(q) - q.quote_at).total_seconds() <= self._max_quote_age_s
                and isfinite(q.bid_price)
                and isfinite(q.ask_price)
                and 0 < q.bid_price <= q.ask_price
            ),
            key=self._visible_at,
        )
        if not eligible_quotes:
            return RFQFillResult(
                fill_id=fill_id,
                request_id=request.request_id,
                is_filled=False,
                rejection_reason="NO_ELIGIBLE_QUOTES_AFTER_DELAY",
            )

        target_quote = eligible_quotes[0]
        mid = (target_quote.bid_price + target_quote.ask_price) / 2.0
        spread = target_quote.ask_price - target_quote.bid_price
        spread_pct = spread / mid if mid > 0 else 0.0

        # 2. 评估成交概率
        fill_prob = self._compute_fill_probability(target_quote, request, spread_pct)
        if fill_prob < 0.5:
            return RFQFillResult(
                fill_id=fill_id,
                request_id=request.request_id,
                is_filled=False,
                rejection_reason=f"REJECTED_DUE_TO_LOW_ACCEPTANCE_PROBABILITY_{fill_prob:.2f}",
            )

        # 3. 计算实际成交价（客户买入看 ask_price，客户卖出看 bid_price）
        base_price = target_quote.ask_price if request.direction == TradeDirection.BUY else target_quote.bid_price
        slippage = self._estimate_slippage(base_price, request.is_programmatic)

        if request.direction == TradeDirection.BUY:
            final_price = base_price + slippage
            fee_rate = self._fee_rule.buy_fee_rate
        else:
            final_price = max(base_price - slippage, 0.0)
            fee_rate = self._fee_rule.sell_fee_rate

        final_price = round(final_price, 4)
        total_trade_amount = request.grams * final_price
        fee_cny = round(total_trade_amount * fee_rate, 2)

        return RFQFillResult(
            fill_id=fill_id,
            request_id=request.request_id,
            is_filled=True,
            filled_price=final_price,
            filled_grams=request.grams,
            fee_cny=fee_cny,
            slippage_cny=slippage,
            filled_at=self._visible_at(target_quote),
        )
