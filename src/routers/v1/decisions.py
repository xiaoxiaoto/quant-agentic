"""决策与风控评估 RESTful 接口路由。

触发全流水线元决策评估并输出决策指令。
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from src.contracts.domains.feature import ModelPrediction
from src.contracts.enums.trading import HoldingPeriod
from src.contracts.schemas import (
    DecisionEvaluateRequest,
    DecisionEvaluateResponse,
)
from src.runtime.dependencies import RuntimeDependencies

router = APIRouter(prefix="/decisions", tags=["decisions"])


@router.post("/evaluate", response_model=DecisionEvaluateResponse)
def evaluate_decision(
    payload: DecisionEvaluateRequest, runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)]
) -> DecisionEvaluateResponse:
    """对输入行情执行全流程特征工程、模型预测、元决策与风控核验。"""
    # 1. 提取特征
    now = datetime.now(UTC)
    if (
        max(payload.quote.quote_at, payload.quote.received_at, payload.quote.available_at or payload.quote.received_at)
        > now
    ):
        raise HTTPException(status_code=422, detail="决策报价的时间与可用时间不能晚于当前时点")
    history = runtime.quotes.query_pit_series(
        start_time=now - timedelta(hours=max(720, runtime.settings.timesfm_context_length + 1)),
        end_time=payload.quote.quote_at,
        as_of=now,
        product_code=payload.quote.product_code,
    )
    history = [q for q in history if q.source_id == payload.quote.source_id and q.quote_id != payload.quote.quote_id]
    history.append(payload.quote)
    try:
        feats = runtime.features.extract_features(history, as_of=now)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="TimesFM 未生成有效特征，请检查模型状态与连续小时行情") from exc
    if runtime.settings.timesfm_enabled and feats.timesfm_forecast_return is None:
        raise HTTPException(status_code=503, detail="TimesFM 未生成有效特征，请检查模型状态与连续小时行情")

    # 2. 预测
    try:
        pred = runtime.predictor.predict(feats, horizon=HoldingPeriod.W1)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="已配置预测模型不可用，未生成交易建议") from exc
    preds: dict[HoldingPeriod, ModelPrediction] = {HoldingPeriod.W1: pred}

    # 3. 决策
    snap = runtime.ledger.take_snapshot(now)
    raw_dec = runtime.decision.evaluate_decision(
        quote=payload.quote,
        predictions=preds,
        account=snap,
        target_buy_grams=payload.target_buy_grams,
        as_of=now,
    )

    # 4. 独立风控
    approved_dec, _, violations = runtime.risk.check_decision(
        decision=raw_dec,
        account=snap,
        current_ask_price=payload.quote.ask_price,
        current_bid_price=payload.quote.bid_price,
    )

    return DecisionEvaluateResponse(
        raw_decision=raw_dec,
        approved_decision=approved_dec,
        risk_violations=violations,
        prediction=pred,
    )
