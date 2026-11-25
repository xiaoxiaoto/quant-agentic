"""双引擎模拟交易编排服务。

支持两套模拟盘执行路径：
- 本地影子模拟盘（PaperEngine.LOCAL）：串联 PIT 行情、特征、预测、决策、风控、ExecutionAdapter 与 RFQ 成交仿真。
- 京东黄金远程模拟盘（PaperEngine.JD）：转发 JDGoldTradeService.execute_sim_*，并强制急停与显式确认门禁。
"""

import uuid
from datetime import UTC, datetime, timedelta

from pydantic import JsonValue

from src.contracts.domains.account import OrderEvent
from src.contracts.domains.execution import PaperStepResult, RFQFillResult, RFQRequest
from src.contracts.domains.feature import ModelPrediction
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.reference_quote import ReferenceQuote
from src.contracts.enums.jd_gold import Bank, HoldingBank, SimAccountType, SimTradeUnit
from src.contracts.enums.trading import DataQualityState, DecisionAction, HoldingPeriod, TradeDirection
from src.repositories.quote import QuoteRepository
from src.services.decision.decision_engine import DecisionEngine
from src.services.execution.execution_adapter import ExecutionAdapter
from src.services.execution.rfq_execution import RFQExecutionEngine
from src.services.execution.shadow_ledger import ShadowLedger
from src.services.jd_gold.gateway import JDGoldGateway
from src.services.market.live_quotes import LiveQuoteCollector
from src.services.prediction.feature_engine import FeatureEngine
from src.services.prediction.predictor import ModelPredictor
from src.services.risk.risk_engine import RiskEngine


class PaperTradingService:
    """本地影子模拟盘与京东远程模拟盘统一编排服务。"""

    def __init__(
        self,
        quotes: QuoteRepository,
        ledger: ShadowLedger,
        feature_engine: FeatureEngine,
        predictor: ModelPredictor,
        decision_engine: DecisionEngine,
        risk_engine: RiskEngine,
        rfq_engine: RFQExecutionEngine,
        execution_adapter: ExecutionAdapter,
        jd_gold: JDGoldGateway,
        *,
        live_quotes: LiveQuoteCollector | None = None,
        synthetic_quote_enabled: bool = False,
        synthetic_reference_product: str = "CMBC-JCJ",
        synthetic_spread_pct: float = 0.002,
        basis_reference_product: str = "SGE-Au99.99",
        timesfm_enabled: bool = False,
        timesfm_context_length: int = 512,
    ) -> None:
        self._quotes = quotes
        self._ledger = ledger
        self._features = feature_engine
        self._predictor = predictor
        self._decision = decision_engine
        self._risk = risk_engine
        self._rfq = rfq_engine
        self._execution = execution_adapter
        self._jd_gold = jd_gold
        self._live_quotes = live_quotes
        self._synthetic_quote_enabled = synthetic_quote_enabled
        self._synthetic_reference_product = synthetic_reference_product
        self._synthetic_spread_pct = synthetic_spread_pct
        self._basis_reference_product = basis_reference_product
        self._timesfm_enabled = timesfm_enabled
        self._timesfm_context_length = timesfm_context_length

    def _now(self) -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _visible_at(quote: QuoteRaw) -> datetime:
        return max(quote.quote_at, quote.received_at, quote.available_at or quote.received_at)

    def _resolve_local_quote(self, *, product_code: str, as_of: datetime) -> QuoteRaw | None:
        """优先读取真实双边报价；开启合成桥接开关时与实时参考价比较新旧，谁更新使用谁。"""
        real_quote = self._quotes.get_latest_as_of(as_of=as_of, product_code=product_code, exclude_synthetic=True)
        if not self._synthetic_quote_enabled:
            return real_quote

        synthetic_quote = self._quotes.get_latest_as_of(as_of=as_of, product_code=product_code, exclude_synthetic=False)
        if synthetic_quote is not None and not synthetic_quote.is_synthetic:
            synthetic_quote = None

        if self._live_quotes is not None:
            ref = self._live_quotes.latest(self._synthetic_reference_product)
            if (
                ref is not None
                and ref.unit == "CNY/g"
                and ref.price > 0
                and ref.quality_state != DataQualityState.ANOMALY
            ):
                candidate = self.build_synthetic_quote(
                    ref,
                    product_code=product_code,
                    spread_pct=self._synthetic_spread_pct,
                    as_of=as_of,
                )
                if synthetic_quote is None or self._visible_at(candidate) > self._visible_at(synthetic_quote):
                    self._quotes.save_quote(candidate)
                    synthetic_quote = candidate

        if real_quote is None:
            return synthetic_quote
        if synthetic_quote is None:
            return real_quote
        if self._visible_at(synthetic_quote) > self._visible_at(real_quote):
            return synthetic_quote
        return real_quote

    def _resolve_basis_ref_cny(self, as_of: datetime) -> float | None:
        """读取最新 CNY/g 基准参考价（默认上金所 Au99.99）以计算内外盘基差特征。"""
        if self._live_quotes is None:
            return None
        try:
            ref = self._live_quotes.latest(self._basis_reference_product)
        except ValueError:
            return None
        if ref is None or ref.unit != "CNY/g" or ref.price <= 0:
            return None
        if ref.quality_state == DataQualityState.ANOMALY:
            return None
        visible_at = ref.available_at or ref.received_at
        if visible_at > as_of:
            return None
        return ref.price

    def _load_history_for_quote(self, quote: QuoteRaw, as_of: datetime) -> list[QuoteRaw]:
        """按来源、产品与 PIT 约束加载用于特征提取的历史序列。"""
        lookback_hours = max(720, self._timesfm_context_length + 1)
        history = self._quotes.query_pit_series(
            start_time=quote.quote_at - timedelta(hours=lookback_hours),
            end_time=quote.quote_at,
            as_of=as_of,
            product_code=quote.product_code,
            exclude_synthetic=not quote.is_synthetic,
        )
        filtered = [q for q in history if q.source_id == quote.source_id and q.quote_id != quote.quote_id]
        filtered.append(quote)
        return filtered

    @staticmethod
    def build_synthetic_quote(
        reference: ReferenceQuote,
        *,
        product_code: str = "GOLD_ACCUMULATION",
        spread_pct: float = 0.002,
        as_of: datetime | None = None,
    ) -> QuoteRaw:
        """将 CNY/g 单边参考价按配置点差合成为仅限模拟盘使用的双边报价。"""
        if reference.unit != "CNY/g":
            raise ValueError(f"仅支持 CNY/g 单位的参考行情合成双边报价，当前单位：{reference.unit}")
        half = spread_pct / 2.0
        bid_price = round(reference.price * (1.0 - half), 4)
        ask_price = round(reference.price * (1.0 + half), 4)
        source_time = reference.quote_at or reference.received_at
        effective_received = reference.received_at
        if as_of is not None:
            source_time = min(source_time, as_of)
            effective_received = min(effective_received, as_of)
        return QuoteRaw(
            quote_id=f"syn_{reference.product_code}_{reference.quote_id}",
            source_id=f"SYNTHETIC_{reference.product_code}",
            product_code=product_code,
            bid_price=bid_price,
            ask_price=ask_price,
            quote_at=source_time,
            received_at=effective_received,
            available_at=effective_received,
            is_tradable=True,
            is_synthetic=True,
            payload_hash=reference.payload_hash,
            quality_state=DataQualityState.NORMAL,
        )

    def step_local(
        self,
        *,
        product_code: str = "GOLD_ACCUMULATION",
        trade_grams: float = 2.0,
        quote: QuoteRaw | None = None,
        as_of: datetime | None = None,
    ) -> PaperStepResult:
        """执行单步本地影子模拟交易评估、风控核验与 RFQ 撮合。"""
        now = as_of or self._now()
        if self._execution.is_kill_switch_active():
            return PaperStepResult(
                as_of=now,
                product_code=product_code,
                risk_violations=["KILL_SWITCH_ACTIVE"],
                snapshot=self._ledger.take_snapshot(now),
                message="人工急停开关已激活，已阻断本地模拟交易执行",
            )

        target_quote = quote or self._resolve_local_quote(product_code=product_code, as_of=now)
        if target_quote is None:
            return PaperStepResult(
                as_of=now,
                product_code=product_code,
                snapshot=self._ledger.take_snapshot(now),
                message=f"未找到标的 {product_code} 在当前时点可用的 PIT 报价，跳过本次模拟步骤",
            )

        history = self._load_history_for_quote(target_quote, as_of=now)
        basis_ref_cny = self._resolve_basis_ref_cny(now)
        feats = self._features.extract_features(history, intl_ref_cny=basis_ref_cny, as_of=now)
        if self._timesfm_enabled and feats.timesfm_forecast_return is None:
            raise RuntimeError("TimesFM 未生成有效特征，请检查模型状态与连续小时行情")

        pred = self._predictor.predict(feats, horizon=HoldingPeriod.W1)
        preds: dict[HoldingPeriod, ModelPrediction] = {HoldingPeriod.W1: pred}

        snap = self._ledger.take_snapshot(now)
        raw_dec = self._decision.evaluate_decision(
            quote=target_quote,
            predictions=preds,
            account=snap,
            target_buy_grams=trade_grams,
            as_of=now,
        )
        daily_pnl, weekly_pnl = self._ledger.compute_period_pnl(now, target_quote.bid_price)
        approved_dec, _, violations = self._risk.check_decision(
            decision=raw_dec,
            account=snap,
            current_ask_price=target_quote.ask_price,
            current_bid_price=target_quote.bid_price,
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
        )

        order: OrderEvent | None = None
        fill: RFQFillResult | None = None
        message = f"评估完成，决策动作：{approved_dec.action.value}"

        if approved_dec.action in (DecisionAction.BUY, DecisionAction.SELL):
            direction = TradeDirection.BUY if approved_dec.action == DecisionAction.BUY else TradeDirection.SELL
            client_order_id = f"paper_{uuid.uuid4().hex[:12]}"
            order = self._execution.submit_order(
                client_order_id=client_order_id,
                direction=direction,
                grams=abs(approved_dec.delta_g),
            )
            rfq_req = RFQRequest(
                request_id=client_order_id,
                direction=direction,
                grams=abs(approved_dec.delta_g),
                decision_time=now,
                eligible_at=now,
                is_programmatic=True,
            )
            fill = self._rfq.simulate_execution(rfq_req, candidate_quotes=[target_quote], allow_prior_quote=True)
            order = self._execution.process_fill(client_order_id=client_order_id, fill_result=fill)
            if fill.is_filled:
                self._ledger.mark_to_market(target_quote.bid_price)
                message = (
                    f"本地模拟成交：{direction.value} {fill.filled_grams}g @ {fill.filled_price}，"
                    f"手续费 {fill.fee_cny} 元"
                )
            else:
                message = f"本地模拟未成交：{fill.rejection_reason or 'UNKNOWN'}"

        return PaperStepResult(
            as_of=now,
            product_code=target_quote.product_code,
            quote_id=target_quote.quote_id,
            prediction=pred,
            raw_decision=raw_dec,
            approved_decision=approved_dec,
            risk_violations=violations,
            order=order,
            fill=fill,
            snapshot=self._ledger.take_snapshot(now),
            message=message,
        )

    def submit_local_order(
        self,
        *,
        direction: TradeDirection,
        grams: float,
        product_code: str = "GOLD_ACCUMULATION",
        quote: QuoteRaw | None = None,
        as_of: datetime | None = None,
    ) -> tuple[OrderEvent, RFQFillResult]:
        """手动提交一笔本地影子模拟盘委托并按当前 PIT 报价撮合。"""
        now = as_of or self._now()
        target_quote = quote or self._resolve_local_quote(product_code=product_code, as_of=now)
        if target_quote is None:
            raise ValueError(f"未找到标的 {product_code} 的可用 PIT 报价，无法完成本地模拟撮合")

        client_order_id = f"paper_manual_{uuid.uuid4().hex[:10]}"
        self._execution.submit_order(
            client_order_id=client_order_id,
            direction=direction,
            grams=grams,
        )
        # 以实际下单时点为决策和可执行时间，RFQ 引擎将按该时点校验报价是否过期
        rfq_req = RFQRequest(
            request_id=client_order_id,
            direction=direction,
            grams=grams,
            decision_time=now,
            eligible_at=now,
            is_programmatic=False,
        )
        fill = self._rfq.simulate_execution(rfq_req, candidate_quotes=[target_quote], allow_prior_quote=True)
        order = self._execution.process_fill(client_order_id=client_order_id, fill_result=fill)
        if fill.is_filled:
            self._ledger.mark_to_market(target_quote.bid_price)
        return order, fill

    def get_local_status(self) -> dict[str, object]:
        """查看本地影子模拟盘账户快照、订单数量与急停状态。"""
        snapshot = self._ledger.take_snapshot()
        orders = self._ledger.get_order_history()
        return {
            "engine": "local",
            "kill_switch_active": self._execution.is_kill_switch_active(),
            "order_count": len(orders),
            "snapshot": snapshot.model_dump(mode="json"),
        }

    async def get_jd_status(
        self,
        *,
        account_type: SimAccountType | int = SimAccountType.DEFAULT,
        page_size: int = 8,
    ) -> dict[str, JsonValue]:
        """查询京东黄金远程模拟盘账户、概览与最近交易流水。"""
        account = await self._jd_gold.trade.get_or_init_sim_account(account_type=account_type)
        overview = await self._jd_gold.trade.get_sim_trade_overview(account_type=account_type)
        records = await self._jd_gold.trade.list_sim_trade_records(page_size=page_size, account_type=account_type)
        return {
            "engine": "jd",
            "kill_switch_active": self._execution.is_kill_switch_active(),
            "account": account,
            "overview": overview,
            "records": records,
        }

    async def execute_jd_order(
        self,
        *,
        direction: TradeDirection,
        bank: HoldingBank | Bank | str = HoldingBank.CZB,
        grams: float | None = None,
        amount: float | None = None,
        ratio: float | None = None,
        account_type: SimAccountType | int = SimAccountType.DEFAULT,
        confirm: bool = False,
    ) -> JsonValue:
        """执行京东黄金远程模拟盘买入或卖出，强制要求显式确认并受急停保护。"""
        if not confirm:
            raise ValueError("执行京东黄金远程模拟盘下单必须显式确认（请传入 --confirm）")
        if self._execution.is_kill_switch_active():
            raise RuntimeError("系统处于人工急停状态，严禁提交京东模拟盘委托")

        if direction == TradeDirection.BUY:
            if grams is not None and amount is not None:
                raise ValueError("买入仅允许指定克重或金额之一")
            if grams is not None:
                return await self._jd_gold.trade.execute_sim_buy(
                    SimTradeUnit.GRAM,
                    bus_id=bank,
                    trade_gram=grams,
                    account_type=account_type,
                )
            if amount is not None:
                return await self._jd_gold.trade.execute_sim_buy(
                    SimTradeUnit.AMOUNT,
                    bus_id=bank,
                    trade_amount=amount,
                    account_type=account_type,
                )
            raise ValueError("京东模拟买入必须指定 --grams 或 --amount")

        if grams is not None and ratio is not None:
            raise ValueError("卖出仅允许指定克重或比例之一")
        if grams is not None:
            return await self._jd_gold.trade.execute_sim_sell(
                SimTradeUnit.GRAM,
                bus_id=bank,
                trade_gram=grams,
                account_type=account_type,
            )
        if ratio is not None:
            return await self._jd_gold.trade.execute_sim_sell(
                SimTradeUnit.RATIO,
                bus_id=bank,
                trade_ratio=ratio,
                account_type=account_type,
            )
        raise ValueError("京东模拟卖出必须指定 --grams 或 --ratio")
