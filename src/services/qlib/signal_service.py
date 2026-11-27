"""Qlib 盘前单标的择时信号生成与人工复核提醒推送服务。"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar, cast
from uuid import uuid4

from src.agents.qlib import QlibAgent
from src.contracts.domains.decision import Decision
from src.contracts.domains.qlib import QlibFactorPreset, QlibSignalRecord
from src.contracts.domains.quote import QuoteRaw
from src.contracts.enums.trading import DataQualityState, DecisionAction, HoldingPeriod
from src.contracts.exceptions import QlibDatasetError
from src.contracts.schemas.qlib import QlibSignalRequest, QlibSignalResponse
from src.services.execution.shadow_ledger import ShadowLedger
from src.services.qlib.research_service import QlibResearchService
from src.services.risk.alert_service import AlertService


class QlibSignalService:
    """加载已校准 Qlib 产物生成 Au99.99 盘前择时信号，可选写入人工复核提醒队列（严禁自动下单）。"""

    _VALID_PRESETS: ClassVar[tuple[str, ...]] = ("gold_core", "alpha158_lite", "au9999_timing")
    _DEFAULT_MAX_SIGNAL_AGE: ClassVar[timedelta] = timedelta(days=5)

    def __init__(
        self,
        research: QlibResearchService,
        *,
        alerts: AlertService | None = None,
        ledger: ShadowLedger | None = None,
        default_model_dir: str | None = None,
        default_buy_thresh: float = 0.001,
        default_sell_thresh: float = -0.0005,
        default_trade_grams: float = 10.0,
        default_spread_pct: float = 0.002,
        max_signal_age: timedelta | None = _DEFAULT_MAX_SIGNAL_AGE,
    ) -> None:
        self._research = research
        self._alerts = alerts
        self._ledger = ledger
        self._default_model_dir = default_model_dir
        self._default_buy_thresh = default_buy_thresh
        self._default_sell_thresh = default_sell_thresh
        self._default_trade_grams = default_trade_grams
        self._default_spread_pct = default_spread_pct
        self._max_signal_age = max_signal_age

    def _resolve_model_dir(self, override: Path | None, requested: str | None) -> Path:
        if override is not None:
            return override
        raw = requested or self._default_model_dir
        if not raw:
            raise QlibDatasetError("未指定 Qlib 模型目录，请传入 model_dir 或配置 QLIB_MODEL_DIR")
        return Path(raw)

    def _publish_review_alert(
        self,
        *,
        signal_id: str,
        instrument: str,
        as_of: datetime,
        latest_close: float,
        action: DecisionAction,
        trade_grams: float,
        prediction_return: float,
        prediction_lcb: float,
        win_prob: float,
        uncertainty: float,
        horizon: HoldingPeriod,
    ) -> str | None:
        if self._alerts is None or self._ledger is None or action not in (DecisionAction.BUY, DecisionAction.SELL):
            return None
        now_utc = datetime.now(UTC)
        as_of_utc = as_of.astimezone(UTC) if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
        if self._max_signal_age is not None and (now_utc - as_of_utc) > self._max_signal_age:
            return None
        snapshot = self._ledger.take_snapshot(as_of=now_utc)
        half = self._default_spread_pct / 2.0
        bid_price = round(latest_close * (1.0 - half), 4)
        ask_price = round(latest_close * (1.0 + half), 4)
        if action == DecisionAction.SELL:
            if snapshot.gold_available_g <= 0.0:
                return None
            delta_g = -min(trade_grams, snapshot.gold_available_g)
        else:
            if ask_price <= 0.0 or snapshot.cash_available <= 0.0:
                return None
            max_affordable_g = math.floor((snapshot.cash_available / ask_price) * 10000.0) / 10000.0
            if max_affordable_g <= 0.0:
                return None
            delta_g = min(trade_grams, max_affordable_g)
        digest = hashlib.sha256(f"{signal_id}:{instrument}:{as_of.isoformat()}:{latest_close}".encode()).hexdigest()
        quote = QuoteRaw(
            quote_id=f"qlib_sig_{signal_id}",
            source_id=f"QLIB_SIGNAL_{instrument}",
            product_code=instrument,
            bid_price=bid_price,
            ask_price=ask_price,
            quote_at=as_of,
            received_at=now_utc,
            available_at=as_of,
            is_tradable=False,
            is_synthetic=True,
            payload_hash=digest,
            quality_state=DataQualityState.NORMAL,
        )
        target_g = max(0.0, snapshot.gold_total_g + delta_g)
        decision = Decision(
            decision_id=f"qlib_dec_{signal_id}",
            decision_at=now_utc,
            action=action,
            delta_g=round(delta_g, 4),
            target_inventory_g=round(target_g, 4),
            horizon_primary=horizon,
            forecast_ids=[signal_id],
            expected_net_return=round(prediction_return, 6),
            net_return_lcb=round(prediction_lcb, 6),
            win_probability=round(win_prob, 4),
            expected_cost=round(self._default_spread_pct, 6),
            uncertainty=round(uncertainty, 6),
            reason_codes=["QLIB_AU9999_PREMARKET_SIGNAL"],
            expires_at=now_utc + timedelta(hours=6),
        )
        alert = self._alerts.create_alert(
            decision,
            quote,
            snapshot,
            validity_minutes=360.0,
            is_reference_estimated=True,
        )
        return alert.alert_id if alert is not None else None

    def generate_signal(
        self,
        request: QlibSignalRequest,
        *,
        provider_uri_override: Path | None = None,
        model_dir_override: Path | None = None,
    ) -> QlibSignalResponse:
        """提取最新特征并调用指定 QlibAgent 产物生成盘前择时信号。"""
        model_path = self._resolve_model_dir(model_dir_override, request.model_dir)
        agent = QlibAgent(model_path, allow_non_serving=True)
        info = agent.get_model_info()
        preset_raw = str(info.get("factor_preset", "au9999_timing"))
        if preset_raw not in self._VALID_PRESETS:
            raise ValueError(f"模型预设 {preset_raw} 暂不支持自动特征提取，仅支持 {self._VALID_PRESETS}")
        preset = cast(QlibFactorPreset, preset_raw)

        as_of, latest_close, feature_map = self._research.fetch_latest_features(
            source_type=request.source_type,
            provider_uri_override=provider_uri_override,
            provider_uri_raw=request.provider_uri,
            instrument=request.instrument,
            freq=request.freq,
            factor_preset=preset,
        )
        prediction = agent.predict_feature_map(feature_map, as_of=as_of, horizon=request.horizon)

        buy_thresh = self._default_buy_thresh if request.buy_thresh is None else request.buy_thresh
        sell_thresh = self._default_sell_thresh if request.sell_thresh is None else request.sell_thresh
        if prediction.expected_net_return > buy_thresh and (
            not request.use_lcb_gate or prediction.net_return_lcb > 0.0
        ):
            action = DecisionAction.BUY
        elif prediction.expected_net_return < sell_thresh:
            action = DecisionAction.SELL
        else:
            action = DecisionAction.HOLD

        signal_id = f"qsig_{uuid4().hex[:12]}"
        alert_id: str | None = None
        if request.publish_alert and action in (DecisionAction.BUY, DecisionAction.SELL):
            grams = request.trade_grams or self._default_trade_grams
            alert_id = self._publish_review_alert(
                signal_id=signal_id,
                instrument=request.instrument,
                as_of=as_of,
                latest_close=latest_close,
                action=action,
                trade_grams=grams,
                prediction_return=prediction.expected_net_return,
                prediction_lcb=prediction.net_return_lcb,
                win_prob=prediction.win_probability,
                uncertainty=prediction.uncertainty,
                horizon=request.horizon,
            )

        record = QlibSignalRecord(
            signal_id=signal_id,
            instrument=request.instrument,
            as_of=as_of,
            horizon=request.horizon,
            predicted_return=round(prediction.expected_net_return, 6),
            net_return_lcb=round(prediction.net_return_lcb, 6),
            win_probability=round(prediction.win_probability, 4),
            action=action,
            buy_thresh=buy_thresh,
            sell_thresh=sell_thresh,
            latest_close=round(latest_close, 4),
            model_dir=str(model_path),
            alert_id=alert_id,
            feature_snapshot=feature_map,
            created_at=datetime.now(UTC),
        )
        return QlibSignalResponse(signal=record)
