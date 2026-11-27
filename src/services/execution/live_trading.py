"""实盘辅助闭环编排服务。

遵循「实时行情评估 → 合规提醒生成 → 人工确认/标记成交 → 实盘持仓查询与对账」闭环。
严禁未经授权的真实资金自动下单；对账发现差异时自动切换为 HALT_RECONCILE 并激活 Kill-Switch。
"""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta, timezone
from math import isfinite
from threading import RLock

from pydantic import JsonValue

from src.clients.jd_gold import JDGoldError
from src.contracts.domains.account import AccountSnapshot, BankStatementItem, OrderEvent, ReconciliationReport
from src.contracts.domains.alert import AlertPayload
from src.contracts.domains.execution import BatchOrderInstruction, LiveStepResult
from src.contracts.domains.feature import ModelPrediction
from src.contracts.domains.quote import QuoteRaw
from src.contracts.domains.reference_quote import ReferenceQuote
from src.contracts.enums.trading import (
    AlertState,
    DataQualityState,
    DecisionAction,
    HoldingPeriod,
    OrderState,
    RiskState,
    TradeDirection,
)
from src.repositories.database import SQLiteDatabase
from src.repositories.quote import QuoteRepository
from src.repositories.runtime_state import RuntimeStateRepository
from src.services.decision.decision_engine import DecisionEngine
from src.services.execution.execution_adapter import ExecutionAdapter
from src.services.execution.reconciliation import ReconciliationService
from src.services.execution.shadow_ledger import ShadowLedger
from src.services.jd_gold.gateway import JDGoldGateway
from src.services.market.live_quotes import LiveQuoteCollector
from src.services.market.session_calendar import SessionCalendar
from src.services.prediction.feature_engine import FeatureEngine
from src.services.prediction.predictor import ModelPredictor
from src.services.risk.alert_service import AlertService
from src.services.risk.risk_engine import RiskEngine


class LiveTradingService:
    """实盘辅助信号、人工确认提醒与对账风控闭环服务。"""

    def __init__(
        self,
        quotes: QuoteRepository,
        ledger: ShadowLedger,
        feature_engine: FeatureEngine,
        predictor: ModelPredictor,
        decision_engine: DecisionEngine,
        risk_engine: RiskEngine,
        alert_service: AlertService,
        reconciliation_service: ReconciliationService,
        execution_adapter: ExecutionAdapter,
        jd_gold: JDGoldGateway,
        live_quotes: LiveQuoteCollector,
        *,
        state_store: RuntimeStateRepository | None = None,
        session_calendar: SessionCalendar | None = None,
        live_reference_assist_enabled: bool = False,
        live_reference_spread_pct: float = 0.002,
        basis_reference_product: str = "SGE-Au99.99",
        default_initial_cash: float = 100000.0,
        timesfm_enabled: bool = False,
        timesfm_context_length: int = 512,
    ) -> None:
        self._lock = RLock()
        self._quotes = quotes
        self._ledger = ledger
        self._features = feature_engine
        self._predictor = predictor
        self._decision = decision_engine
        self._risk = risk_engine
        self._alerts = alert_service
        self._reconciliation = reconciliation_service
        self._execution = execution_adapter
        self._jd_gold = jd_gold
        self._live_quotes = live_quotes
        self._state_store = state_store
        self._calendar = session_calendar or SessionCalendar()
        self._live_reference_assist_enabled = live_reference_assist_enabled
        self._live_reference_spread_pct = live_reference_spread_pct
        self._basis_reference_product = basis_reference_product
        self._default_initial_cash = default_initial_cash
        self._timesfm_enabled = timesfm_enabled
        self._timesfm_context_length = timesfm_context_length
        self._risk_state: RiskState = RiskState.NORMAL
        self._last_reconciliation: ReconciliationReport | None = None
        self._last_signal_at: datetime | None = None
        if self._state_store is not None:
            saved_risk, _ = self._state_store.load_control_state()
            self._risk_state = saved_risk
            self._last_reconciliation = self._state_store.load_latest_reconciliation_report()
            self._last_signal_at = self._state_store.load_last_signal_at()

    def _apply_ledger_reset(
        self,
        *,
        effective_cash: float,
        jd_orders: list[OrderEvent],
        total_g: float | None,
        holdings: list[tuple[str, float, float]],
    ) -> AccountSnapshot:
        """在服务锁保护下重置本地影子账本，供异步入口卸载至工作线程执行。"""
        with self._lock:
            if jd_orders:
                return self._ledger.reset_and_replay_orders(
                    initial_cash=effective_cash,
                    orders=jd_orders,
                    target_gold_g=total_g,
                )
            return self._ledger.reset_and_seed_holdings(initial_cash=effective_cash, holdings=holdings)

    def _reconcile_jd_holdings_sync(
        self,
        *,
        bank_cash: float | None,
        bank_gold_g: float,
        bank_cost_basis: float | None,
        as_of: datetime | None,
    ) -> tuple[ReconciliationReport, RiskState]:
        """在工作线程中执行本地快照读取与对账写库，避免阻塞异步事件循环。"""
        local_snapshot = self._ledger.take_snapshot(as_of)
        effective_cash = local_snapshot.cash_available if bank_cash is None else bank_cash
        return self.reconcile_snapshot(
            bank_cash=effective_cash,
            bank_gold_g=bank_gold_g,
            bank_cost_basis=bank_cost_basis,
            as_of=as_of,
        )

    def _now(self) -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _to_utc(dt: datetime) -> datetime:
        """统一转换为 UTC aware 时间，避免跨时区或 naive 时间比较报错。"""
        return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    def _persist_control(self) -> None:
        if self._state_store is not None:
            self._state_store.save_control_state(
                self._risk_state,
                self._execution.is_kill_switch_active(),
                last_signal_at=self._last_signal_at,
            )

    @staticmethod
    def _extract_float(data: Mapping[str, object], keys: tuple[str, ...]) -> float | None:
        for key in keys:
            raw = data.get(key)
            if raw is None or isinstance(raw, bool):
                continue
            try:
                value = float(str(raw))
            except ValueError:
                continue
            if not isfinite(value):
                continue
            return value
        return None

    @classmethod
    def _extract_jd_holdings_totals(cls, payload: JsonValue) -> tuple[float, float | None]:
        """从京东持仓响应中提取总黄金克重与可选加权成本均价。"""
        if not isinstance(payload, dict):
            raise ValueError("京东持仓响应格式异常，无法解析黄金克重")
        body: object = payload.get("resultData", payload)
        if isinstance(body, dict) and "datas" in body:
            body = body["datas"]
        if isinstance(body, dict) and "data" in body:
            body = body["data"]

        gram_keys = ("totalGramAll", "totalGram", "holdGram", "goldGram", "gram", "totalGoldGram", "availableGram")
        cost_keys = ("avgCostPrice", "avgPrice", "costPrice", "holdCost", "costBasis")

        if isinstance(body, dict):
            total_g = cls._extract_float(body, gram_keys)
            cost_basis = cls._extract_float(body, cost_keys)
            if total_g is not None:
                return total_g, cost_basis
            nested = body.get("holdingList") or body.get("list") or body.get("holdings") or body.get("items")
            if isinstance(nested, list):
                body = nested

        if isinstance(body, list):
            total_g = 0.0
            weighted_cost = 0.0
            cost_g = 0.0
            found = False
            for item in body:
                if not isinstance(item, dict):
                    continue
                grams = cls._extract_float(item, gram_keys)
                if grams is None:
                    continue
                found = True
                total_g += grams
                unit_cost = cls._extract_float(item, cost_keys)
                if unit_cost is not None and grams > 0:
                    weighted_cost += grams * unit_cost
                    cost_g += grams
            if found:
                return round(total_g, 4), (round(weighted_cost / cost_g, 4) if cost_g > 0 else None)

        raise ValueError("京东持仓响应中未找到有效黄金克重字段")

    def _build_reference_estimated_quote(
        self,
        reference: ReferenceQuote,
        *,
        product_code: str,
        as_of: datetime,
    ) -> QuoteRaw:
        """将 CNY/g 参考行情按估算点差构造为仅限内存使用的辅助评估报价（不落盘至真实报价表）。"""
        half = self._live_reference_spread_pct / 2.0
        bid_price = round(reference.price * (1.0 - half), 4)
        ask_price = round(reference.price * (1.0 + half), 4)
        as_of_utc = self._to_utc(as_of)
        source_time = self._to_utc(reference.quote_at or reference.received_at)
        effective_received = self._to_utc(reference.received_at)
        source_time = min(source_time, as_of_utc)
        effective_received = min(effective_received, as_of_utc)
        return QuoteRaw(
            quote_id=f"ref_assist_{reference.product_code}_{reference.quote_id}",
            source_id=f"REF_ASSIST_{reference.product_code}",
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

    def _load_reference_assist_history(
        self,
        reference_product_code: str,
        current_quote: QuoteRaw,
        *,
        product_code: str,
        as_of: datetime,
    ) -> list[QuoteRaw]:
        """从已持久化的参考行情表按 PIT 边界构造有界辅助评估历史序列。"""
        as_of_utc = self._to_utc(as_of)
        lookback_hours = max(720, self._timesfm_context_length + 1)
        max_points = max(2000, self._timesfm_context_length * 4)
        refs = self._live_quotes.query_series(
            reference_product_code,
            start_time=self._to_utc(current_quote.quote_at) - timedelta(hours=lookback_hours),
            end_time=as_of_utc,
            limit=max_points,
        )
        history: list[QuoteRaw] = []
        for item in refs:
            if item.unit != "CNY/g" or item.price <= 0:
                continue
            if item.quality_state in (DataQualityState.ANOMALY, DataQualityState.STALE):
                continue
            visible_at = self._to_utc(item.available_at or item.received_at)
            if visible_at > as_of_utc:
                continue
            mapped = self._build_reference_estimated_quote(item, product_code=product_code, as_of=as_of_utc)
            if mapped.quote_id != current_quote.quote_id:
                history.append(mapped)
        history.append(current_quote)
        return history

    def _resolve_basis_ref_cny(self, as_of: datetime) -> float | None:
        """读取最新 CNY/g 基准参考价（默认上金所 Au99.99）以计算内外盘基差特征。"""
        try:
            ref = self._live_quotes.latest(self._basis_reference_product)
        except ValueError:
            return None
        if ref is None or ref.unit != "CNY/g" or ref.price <= 0:
            return None
        if ref.quality_state == DataQualityState.ANOMALY:
            return None
        visible_at = self._to_utc(ref.available_at or ref.received_at)
        if visible_at > self._to_utc(as_of):
            return None
        return ref.price

    def _load_history_for_quote(self, quote: QuoteRaw, as_of: datetime) -> list[QuoteRaw]:
        """按来源、产品与 PIT 约束加载用于实盘特征提取的历史序列（严格排除合成报价）。"""
        lookback_hours = max(720, self._timesfm_context_length + 1)
        history = self._quotes.query_pit_series(
            start_time=quote.quote_at - timedelta(hours=lookback_hours),
            end_time=quote.quote_at,
            as_of=as_of,
            product_code=quote.product_code,
            exclude_synthetic=True,
        )
        filtered = [q for q in history if q.source_id == quote.source_id and q.quote_id != quote.quote_id]
        filtered.append(quote)
        return filtered

    @staticmethod
    def _unwrap_jd_order_list(payload: JsonValue) -> Sequence[object]:
        """解包京东订单响应多层信封，返回原始订单条目列表。"""
        body: object = payload.get("resultData", payload) if isinstance(payload, dict) else payload
        if isinstance(body, dict) and "datas" in body:
            body = body["datas"]
        if isinstance(body, dict) and "data" in body:
            body = body["data"]
        raw_list = (
            body
            if isinstance(body, list)
            else (
                body.get("tradeOrderVoList") or body.get("orderList") or body.get("list") or body.get("items")
                if isinstance(body, dict)
                else None
            )
        )
        return raw_list if isinstance(raw_list, list) else ()

    @staticmethod
    def _parse_jd_biz_time(raw: object) -> datetime | None:
        """解析京东订单 bizTime（支持数值/字符串毫秒时间戳、ISO-8601 与北京时间格式）。"""
        if isinstance(raw, (int, float)) and raw > 0:
            ts_sec = float(raw) / 1000.0 if raw > 1e11 else float(raw)
            return datetime.fromtimestamp(ts_sec, tz=UTC)
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return None
            if text.isdigit():
                num = float(text)
                if num > 0:
                    ts_sec = num / 1000.0 if num > 1e11 else num
                    return datetime.fromtimestamp(ts_sec, tz=UTC)
            try:
                parsed = datetime.fromisoformat(text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
                return parsed.astimezone(UTC)
            except ValueError:
                return None
        return None

    @classmethod
    def _parse_jd_gold_orders(cls, payload: JsonValue) -> list[OrderEvent]:
        """解析京东历史订单响应，提取已成交的黄金买卖流水（过滤非黄金及失败/退款单）。"""
        raw_list = cls._unwrap_jd_order_list(payload)
        if not raw_list:
            return []

        parsed: list[OrderEvent] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            if str(item.get("businessCode") or "").upper() != "GOLD":
                continue
            trade_type = str(item.get("tradeTypeCode") or "").upper()
            status_code = str(item.get("statusCode") or "").upper()
            if trade_type == "BUY_GOLD" and status_code == "COMPLETE":
                direction = TradeDirection.BUY
            elif trade_type == "SELL_GOLD" and status_code in ("REDEEM_SUCC", "COMPLETE"):
                direction = TradeDirection.SELL
            else:
                continue

            ext_raw = item.get("extInfo")
            ext: Mapping[str, object] = {}
            if isinstance(ext_raw, str) and ext_raw.strip():
                try:
                    loaded = json.loads(ext_raw)
                    if isinstance(loaded, dict):
                        ext = loaded
                except json.JSONDecodeError:
                    ext = {}
            elif isinstance(ext_raw, dict):
                ext = ext_raw

            grams = cls._extract_float(ext, ("og", "gram", "grams")) or cls._extract_float(
                item, ("gram", "grams", "totalGram")
            )
            price = cls._extract_float(ext, ("ogp", "price", "goldPrice")) or cls._extract_float(
                item, ("price", "executionPrice")
            )
            if grams is None or grams <= 0 or price is None or price <= 0:
                continue

            grams_rounded = round(grams, 4)
            if grams_rounded <= 0:
                continue

            all_amount = cls._extract_float(item, ("allAmount", "amount", "tradeAmount"))
            gross = grams_rounded * price
            if all_amount is not None and all_amount >= 0:
                fee = (
                    max(0.0, round(all_amount - gross, 2))
                    if direction == TradeDirection.BUY
                    else max(0.0, round(gross - all_amount, 2))
                )
            else:
                fee = 0.0

            biz_time_raw = item.get("bizTime")
            dt = cls._parse_jd_biz_time(biz_time_raw) or datetime.now(UTC)

            order_id = str(item.get("orderId") or item.get("bizOrderId") or "").strip()
            if not order_id:
                continue
            bank_order_id = str(item.get("bizOrderId") or order_id).strip()
            order_type = str(item.get("subTradeTypeCode") or trade_type or "MARKET").strip()

            parsed.append(
                OrderEvent(
                    client_order_id=f"jd_{order_id}",
                    bank_order_id=bank_order_id,
                    order_type=order_type,
                    direction=direction,
                    requested_grams=grams_rounded,
                    filled_grams=grams_rounded,
                    execution_price=price,
                    fee=fee,
                    state=OrderState.FILLED,
                    bank_event_at=dt,
                    received_at=dt,
                )
            )
        return parsed

    async def _fetch_all_jd_gold_orders(self, *, max_pages: int = 20, page_size: int = 200) -> list[OrderEvent]:
        """分页拉取京东账户全量历史黄金成交流水并按订单号去重。"""
        end_date = self._now().astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        start_date = "2020-01-01"
        by_id: dict[str, OrderEvent] = {}
        for page_no in range(1, max_pages + 1):
            payload = await self._jd_gold.trade.list_orders(
                start_date,
                end_date,
                page_no=page_no,
                page_size=page_size,
            )
            raw_count = len(self._unwrap_jd_order_list(payload))
            for order in self._parse_jd_gold_orders(payload):
                by_id[order.client_order_id] = order
            if raw_count < page_size:
                break
        return list(by_id.values())

    def evaluate_once(
        self,
        *,
        product_code: str = "GOLD_ACCUMULATION",
        trade_grams: float = 2.0,
        reference_product_code: str = "CMBC-JCJ",
        quote: QuoteRaw | None = None,
        as_of: datetime | None = None,
    ) -> LiveStepResult:
        """执行单次实盘信号评估，并在风控通过时生成对外合规提醒。"""
        now = as_of or self._now()
        ref_quote = self._live_quotes.latest(reference_product_code)

        with self._lock:
            current_risk_state = self._risk_state
            kill_active = self._execution.is_kill_switch_active()
            last_signal_at = self._last_signal_at

        if kill_active or current_risk_state == RiskState.HALT_RECONCILE:
            violations: list[str] = []
            if kill_active:
                violations.append("KILL_SWITCH_ACTIVE")
            if current_risk_state == RiskState.HALT_RECONCILE:
                violations.append("HALT_RECONCILE_ACTIVE")
            return LiveStepResult(
                as_of=now,
                product_code=product_code,
                risk_state=current_risk_state,
                kill_switch_active=kill_active,
                risk_violations=violations,
                reference_quote=ref_quote,
                snapshot=self._ledger.take_snapshot(now),
                message="实盘处于急停或对账熔断状态，已阻断信号生成，请核查后解除",
            )

        if quote is not None and quote.is_synthetic:
            raise ValueError("实盘决策严禁使用参考价合成的模拟双边报价")

        target_quote = quote or self._quotes.get_latest_as_of(
            as_of=now,
            product_code=product_code,
            exclude_synthetic=True,
        )
        is_reference_estimated = False
        reference_missing_source_time = False
        if target_quote is None and self._live_reference_assist_enabled:
            if (
                ref_quote is not None
                and ref_quote.unit == "CNY/g"
                and ref_quote.price > 0
                and ref_quote.quality_state in (DataQualityState.NORMAL, DataQualityState.GAP)
                and self._to_utc(ref_quote.available_at or ref_quote.received_at) <= self._to_utc(now)
            ):
                target_quote = self._build_reference_estimated_quote(
                    ref_quote,
                    product_code=product_code,
                    as_of=now,
                )
                is_reference_estimated = True
                reference_missing_source_time = ref_quote.quality_state == DataQualityState.GAP

        if target_quote is None:
            hint = (
                f"未找到标的 {product_code} 的可用 PIT 双边报价，且参考行情 {reference_product_code} 不可用于辅助估算"
                if self._live_reference_assist_enabled
                else f"未找到标的 {product_code} 的可用 PIT 双边报价（参考行情与合成报价不参与实盘决策）"
            )
            return LiveStepResult(
                as_of=now,
                product_code=product_code,
                risk_state=current_risk_state,
                kill_switch_active=kill_active,
                reference_quote=ref_quote,
                snapshot=self._ledger.take_snapshot(now),
                message=hint,
            )

        history = (
            self._load_reference_assist_history(
                reference_product_code,
                target_quote,
                product_code=product_code,
                as_of=now,
            )
            if is_reference_estimated
            else self._load_history_for_quote(target_quote, as_of=now)
        )
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
            last_signal_at=last_signal_at,
        )
        if is_reference_estimated:
            if "REFERENCE_ESTIMATED_QUOTE" not in raw_dec.risk_flags:
                raw_dec.risk_flags.append("REFERENCE_ESTIMATED_QUOTE")
            if reference_missing_source_time and "REFERENCE_SOURCE_TIME_MISSING" not in raw_dec.risk_flags:
                raw_dec.risk_flags.append("REFERENCE_SOURCE_TIME_MISSING")

        daily_pnl, weekly_pnl = self._ledger.compute_period_pnl(now, target_quote.bid_price)
        approved_dec, next_risk_state, violations = self._risk.check_decision(
            decision=raw_dec,
            account=snap,
            current_ask_price=target_quote.ask_price,
            current_bid_price=target_quote.bid_price,
            risk_state=current_risk_state,
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
        )
        if is_reference_estimated and "REFERENCE_ESTIMATED_QUOTE" not in approved_dec.risk_flags:
            approved_dec.risk_flags.append("REFERENCE_ESTIMATED_QUOTE")

        alert: AlertPayload | None = None
        with self._lock:
            # 写入提醒前在同一临界区重新检查急停、对账熔断、账本快照与信号冷却状态，防止并发竞态
            live_kill_active = self._execution.is_kill_switch_active()
            live_risk_state = self._risk_state
            live_snap = self._ledger.take_snapshot(now)
            ledger_changed = (
                live_snap.cash_available != snap.cash_available
                or live_snap.gold_total_g != snap.gold_total_g
                or live_snap.gold_available_g != snap.gold_available_g
            )
            if live_kill_active or live_risk_state == RiskState.HALT_RECONCILE:
                kill_active = live_kill_active
                next_risk_state = live_risk_state
                if live_kill_active and "KILL_SWITCH_ACTIVE" not in violations:
                    violations.append("KILL_SWITCH_ACTIVE")
                if live_risk_state == RiskState.HALT_RECONCILE and "HALT_RECONCILE_ACTIVE" not in violations:
                    violations.append("HALT_RECONCILE_ACTIVE")
                approved_dec = approved_dec.model_copy(
                    update={"action": DecisionAction.HOLD, "delta_g": 0.0, "target_inventory_g": live_snap.gold_total_g}
                )
            elif ledger_changed and approved_dec.action in (DecisionAction.BUY, DecisionAction.SELL):
                if "LEDGER_STATE_CHANGED" not in violations:
                    violations.append("LEDGER_STATE_CHANGED")
                approved_dec = approved_dec.model_copy(
                    update={
                        "action": DecisionAction.ABSTAIN,
                        "delta_g": 0.0,
                        "target_inventory_g": live_snap.gold_total_g,
                    }
                )
            elif approved_dec.action == DecisionAction.BUY and self._decision.is_in_cooldown(now, self._last_signal_at):
                if "COOLDOWN_ACTIVE" not in approved_dec.risk_flags:
                    approved_dec.risk_flags.append("COOLDOWN_ACTIVE")
                if "SIGNAL_COOLDOWN_ACTIVE" not in approved_dec.reason_codes:
                    approved_dec.reason_codes.append("SIGNAL_COOLDOWN_ACTIVE")
                approved_dec = approved_dec.model_copy(
                    update={"action": DecisionAction.HOLD, "delta_g": 0.0, "target_inventory_g": snap.gold_total_g}
                )
            else:
                alert = self._alerts.create_alert(
                    decision=approved_dec,
                    quote=target_quote,
                    account=snap,
                    is_reference_estimated=is_reference_estimated,
                )
                state_changed = next_risk_state != self._risk_state
                if state_changed:
                    self._risk_state = next_risk_state
                if alert is not None:
                    self._last_signal_at = now
                    state_changed = True
                if state_changed:
                    self._persist_control()
        prefix = "【参考价估算辅助】" if is_reference_estimated else ""
        if alert is not None:
            message = (
                f"{prefix}已生成实盘合规提醒 {alert.alert_id}：{alert.action.value} {alert.suggested_grams}g，"
                f"保本价 {alert.break_even_price}（请人工核实官方实时双边报价后再操作）"
            )
        else:
            message = f"{prefix}实盘评估完成，当前建议：{approved_dec.action.value}，未生成交易提醒"

        return LiveStepResult(
            as_of=now,
            product_code=target_quote.product_code,
            quote_id=target_quote.quote_id,
            risk_state=next_risk_state,
            kill_switch_active=kill_active,
            prediction=pred,
            raw_decision=raw_dec,
            approved_decision=approved_dec,
            risk_violations=violations,
            alert=alert,
            reference_quote=ref_quote,
            is_reference_estimated=is_reference_estimated,
            snapshot=self._ledger.take_snapshot(now),
            message=message,
        )

    def list_alerts(self, state: AlertState | None = None, limit: int = 50) -> list[AlertPayload]:
        """查询实盘合规提醒列表。"""
        return self._alerts.list_alerts(state=state, limit=limit)

    def confirm_alert(self, alert_id: str) -> AlertPayload:
        """人工确认交易提醒意向。"""
        return self._alerts.confirm_alert(alert_id)

    def decline_alert(self, alert_id: str) -> AlertPayload:
        """人工拒绝交易提醒建议。"""
        return self._alerts.decline_alert(alert_id)

    def mark_executed(
        self,
        alert_id: str,
        *,
        filled_grams: float | None = None,
        filled_price: float | None = None,
        fee: float | None = None,
        bank_order_id: str | None = None,
    ) -> AlertPayload:
        """标记提醒已在银行官方渠道成交；提供成交价与克重时同步登记到本地影子账本。

        先校验提醒有效性与尝试登记账本，再推进提醒状态，避免登记失败时提醒卡在已执行；
        若已执行状态下使用相同成交明细重试，则幂等返回。
        """
        with self._lock:
            alert = self._alerts.get_alert(alert_id)
            if alert is None:
                raise KeyError(f"未找到指定的提醒条目：{alert_id}")

            has_fill = filled_grams is not None and filled_price is not None
            if alert.state == AlertState.USER_EXECUTED:
                if not has_fill:
                    raise ValueError(f"当前提醒状态不可标记为已执行：{alert.state.value}")
                assert filled_grams is not None
                assert filled_price is not None
                client_order_id = f"live_{alert_id}"
                if alert.action == DecisionAction.BUY:
                    self._ledger.record_buy(
                        client_order_id=client_order_id,
                        grams=filled_grams,
                        price=filled_price,
                        fee=fee,
                        bank_order_id=bank_order_id,
                    )
                elif alert.action == DecisionAction.SELL:
                    self._ledger.record_sell(
                        client_order_id=client_order_id,
                        grams=filled_grams,
                        price=filled_price,
                        fee=fee,
                        bank_order_id=bank_order_id,
                    )
                return alert

            if alert.state not in (AlertState.ALERT_SENT, AlertState.USER_CONFIRMED):
                raise ValueError(f"当前提醒状态不可标记为已执行：{alert.state.value}")
            now_dt = self._now()
            if self._alerts.is_expired(alert, now_dt):
                # 交由提醒服务将其持久化为 EXPIRED 并抛出标准过期异常
                return self._alerts.mark_executed(alert_id, current_time=now_dt)

            if has_fill:
                assert filled_grams is not None
                assert filled_price is not None
                client_order_id = f"live_{alert_id}"
                if alert.action == DecisionAction.BUY:
                    self._ledger.record_buy(
                        client_order_id=client_order_id,
                        grams=filled_grams,
                        price=filled_price,
                        fee=fee,
                        bank_order_id=bank_order_id,
                    )
                elif alert.action == DecisionAction.SELL:
                    self._ledger.record_sell(
                        client_order_id=client_order_id,
                        grams=filled_grams,
                        price=filled_price,
                        fee=fee,
                        bank_order_id=bank_order_id,
                    )

            try:
                return self._alerts.mark_executed(alert_id, current_time=now_dt)
            except Exception:
                if has_fill:
                    self._risk_state = RiskState.HALT_RECONCILE
                    self._execution.trigger_kill_switch("ALERT_PERSIST_INCONSISTENCY")
                    self._persist_control()
                raise

    async def fetch_jd_holdings(self) -> JsonValue:
        """查询京东实盘账户黄金持仓明细。"""
        return await self._jd_gold.trade.get_holdings()

    async def initialize_ledger_from_jd_holdings(self, *, initial_cash: float | None = None) -> AccountSnapshot:
        """拉取京东实盘历史成交流水与持仓并一键初始化本地影子账本。

        优先使用京东真实黄金历史订单流水按时间顺序重放（保留每笔真实买卖单号、克重、单价、手续费与时间），
        并同步补齐积存金生息差额；若账户暂无历史订单流水，则降级按当前持仓快照初始化。
        """
        effective_cash = self._default_initial_cash if initial_cash is None else initial_cash
        payload = await self._jd_gold.trade.get_holdings()
        body: object = payload.get("resultData", payload) if isinstance(payload, dict) else payload
        if isinstance(body, dict) and "datas" in body:
            body = body["datas"]
        if isinstance(body, dict) and "data" in body:
            body = body["data"]

        holdings: list[tuple[str, float, float]] = []
        raw_list = (
            body
            if isinstance(body, list)
            else (
                body.get("holdingList") or body.get("list") or body.get("holdings") or body.get("items")
                if isinstance(body, dict)
                else None
            )
        )
        if isinstance(raw_list, list):
            for item in raw_list:
                if not isinstance(item, dict):
                    continue
                grams = self._extract_float(item, ("totalGramAll", "totalGram", "holdGram", "goldGram", "gram"))
                cost = self._extract_float(item, ("avgCostPrice", "avgPrice", "costPrice", "holdCost"))
                code = str(
                    item.get("bankCode") or item.get("bankName") or item.get("bank") or item.get("productSku") or "BANK"
                ).strip()
                if grams is not None and grams > 0 and cost is not None and cost > 0:
                    holdings.append((code, grams, cost))

        total_g: float | None = None
        if not holdings:
            total_g, avg_cost = self._extract_jd_holdings_totals(payload)
            if total_g > 0:
                if avg_cost is None or avg_cost <= 0:
                    raise ValueError("京东持仓响应缺少有效成本均价，无法核算持仓成本")
                holdings.append(("ALL", total_g, avg_cost))
        else:
            try:
                total_g, _ = self._extract_jd_holdings_totals(payload)
            except ValueError:
                total_g = round(sum(g for _, g, _ in holdings), 4)

        jd_orders: list[OrderEvent] = []
        try:
            jd_orders = await self._fetch_all_jd_gold_orders()
        except JDGoldError:
            jd_orders = []

        return await SQLiteDatabase.run_shielded_write(
            self._apply_ledger_reset,
            effective_cash=effective_cash,
            jd_orders=jd_orders,
            total_g=total_g,
            holdings=holdings,
        )

    async def fetch_jd_orders(
        self,
        *,
        start_date: str,
        end_date: str,
        page_no: int = 1,
        page_size: int = 50,
    ) -> JsonValue:
        """查询京东实盘历史订单列表。"""
        return await self._jd_gold.trade.list_orders(
            start_date,
            end_date,
            page_no=page_no,
            page_size=page_size,
        )

    async def fetch_jd_order_summary(self, *, start_date: str, end_date: str) -> JsonValue:
        """查询京东实盘订单汇总统计。"""
        return await self._jd_gold.trade.get_order_summary(start_date, end_date)

    async def fetch_jd_income_calendar(
        self,
        *,
        time_type: str = "DAY",
        time_value: str = "",
        trade_type: str = "ALL",
    ) -> JsonValue:
        """查询京东实盘收益日历。"""
        return await self._jd_gold.trade.get_income_calendar(
            time_type=time_type,
            time=time_value,
            trade_type=trade_type,
        )

    async def fetch_jd_conditional_orders(
        self,
        *,
        bank: str,
        start_time_ms: int,
        end_time_ms: int,
        page_index: int = 1,
        page_size: int = 10,
    ) -> JsonValue:
        """查询京东实盘条件挂单列表。"""
        return await self._jd_gold.trade.list_conditional_orders(
            bank,
            start_time_ms,
            end_time_ms,
            page_index=page_index,
            page_size=page_size,
        )

    def reconcile_snapshot(
        self,
        *,
        bank_cash: float,
        bank_gold_g: float,
        bank_cost_basis: float | None = None,
        as_of: datetime | None = None,
    ) -> tuple[ReconciliationReport, RiskState]:
        """核对本地影子账本与银行权威快照，发现差异时自动熔断并触发 Kill-Switch。"""
        with self._lock:
            local_snapshot = self._ledger.take_snapshot(as_of)
            report, recommended_risk = self._reconciliation.reconcile_snapshot(
                local_snapshot=local_snapshot,
                bank_cash=bank_cash,
                bank_gold_g=bank_gold_g,
                bank_cost_basis=bank_cost_basis,
                as_of=as_of,
            )
            self._last_reconciliation = report
            if self._state_store is not None:
                self._state_store.save_reconciliation_report(report)
            if recommended_risk == RiskState.HALT_RECONCILE:
                self._risk_state = RiskState.HALT_RECONCILE
                self._execution.trigger_kill_switch("RECONCILIATION_DISCREPANCY")
                self._persist_control()
        return report, recommended_risk

    async def reconcile_with_jd_holdings(
        self,
        *,
        bank_cash: float | None = None,
        as_of: datetime | None = None,
    ) -> tuple[ReconciliationReport, RiskState]:
        """拉取京东实盘持仓并自动映射到影子账本快照对账，差异时自动熔断。"""
        payload = await self.fetch_jd_holdings()
        bank_gold_g, bank_cost_basis = self._extract_jd_holdings_totals(payload)
        return await SQLiteDatabase.run_shielded_write(
            self._reconcile_jd_holdings_sync,
            bank_cash=bank_cash,
            bank_gold_g=bank_gold_g,
            bank_cost_basis=bank_cost_basis,
            as_of=as_of,
        )

    def list_reconciliation_reports(self, limit: int = 50) -> list[ReconciliationReport]:
        """按时间倒序查询持久化对账报告列表。"""
        if self._state_store is not None:
            return self._state_store.list_reconciliation_reports(limit=limit)
        with self._lock:
            return [self._last_reconciliation] if self._last_reconciliation is not None else []

    def reconcile_orders(
        self,
        *,
        bank_statements: list[BankStatementItem],
        local_orders: list[OrderEvent] | None = None,
        as_of: datetime | None = None,
    ) -> tuple[ReconciliationReport, RiskState]:
        """逐笔核对本地影子账本订单流水与银行权威交易流水，差异时自动熔断。"""
        effective_orders = self._ledger.get_order_history() if local_orders is None else local_orders
        report, recommended_risk = self._reconciliation.reconcile_orders(
            local_orders=effective_orders,
            bank_statements=bank_statements,
            as_of=as_of,
        )
        with self._lock:
            self._last_reconciliation = report
            if self._state_store is not None:
                self._state_store.save_reconciliation_report(report)
            if recommended_risk == RiskState.HALT_RECONCILE:
                self._risk_state = RiskState.HALT_RECONCILE
                self._execution.trigger_kill_switch("ORDER_RECONCILIATION_DISCREPANCY")
                self._persist_control()
        return report, recommended_risk

    def register_batch_instruction(
        self,
        *,
        instruction_id: str,
        direction: TradeDirection,
        grams: float,
        limit_price: float,
        target_open: datetime,
    ) -> BatchOrderInstruction:
        """登记非交易时段次开盘批量价挂单指令。"""
        return self._execution.register_batch_instruction(
            instruction_id=instruction_id,
            direction=direction,
            grams=grams,
            limit_price=limit_price,
            target_open=target_open,
        )

    def execute_batch_at_open(
        self,
        *,
        instruction_id: str,
        bank_batch_price: float,
    ) -> tuple[bool, BatchOrderInstruction]:
        """次开盘依据银行批量价核算挂单是否撮合成交。"""
        return self._execution.execute_batch_at_open(
            instruction_id=instruction_id,
            bank_batch_price=bank_batch_price,
        )

    def trigger_kill_switch(self, reason: str = "MANUAL_EMERGENCY_HALT") -> None:
        """手动触发实盘人工急停开关。"""
        with self._lock:
            self._execution.trigger_kill_switch(reason)
            self._persist_control()

    def reset_kill_switch(self) -> None:
        """解除人工急停并恢复实盘风控状态至 NORMAL（须人工复核后调用）。"""
        with self._lock:
            self._execution.reset_kill_switch()
            self._risk_state = RiskState.NORMAL
            self._persist_control()

    def get_status(self) -> dict[str, object]:
        """返回实盘风控状态、急停状态、交易时段、提醒统计、本地账本快照及京东授权摘要。"""
        now = self._now()
        now_ts = now.timestamp()
        with self._lock:
            risk_state = self._risk_state
            last_recon = self._last_reconciliation
        auth_state = self._jd_gold.auth_state()
        alerts = self._alerts.list_alerts(limit=100)
        pending_alerts = sum(1 for item in alerts if item.state in (AlertState.ALERT_SENT, AlertState.USER_CONFIRMED))
        token_present = bool(auth_state.access_token.get_secret_value())
        actual_expiry = auth_state.actual_expires_at
        usable_seconds = max(0, int(actual_expiry - now_ts)) if actual_expiry is not None else None
        token_valid = token_present and (actual_expiry is None or actual_expiry > now_ts)
        token_expired = token_present and (actual_expiry is not None and actual_expiry <= now_ts)
        return {
            "risk_state": risk_state.value,
            "kill_switch_active": self._execution.is_kill_switch_active(),
            "live_reference_assist_enabled": self._live_reference_assist_enabled,
            "basis_reference_product": self._basis_reference_product,
            "default_initial_cash": self._default_initial_cash,
            "sell_fee_rate": self._ledger.sell_fee_rate,
            "is_trading_session": self._calendar.is_trading_time(now),
            "total_alerts": len(alerts),
            "pending_alerts": pending_alerts,
            "jd_logged_in": token_valid,
            "jd_token_expired": token_expired,
            "jd_usable_seconds": usable_seconds,
            "jd_token_expires_at": actual_expiry,
            "last_reconciliation": last_recon.model_dump(mode="json") if last_recon is not None else None,
            "snapshot": self._ledger.take_snapshot(now).model_dump(mode="json"),
        }
