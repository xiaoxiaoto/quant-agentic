"""决策与风控评估 RESTful 接口路由。

触发全流水线元决策评估并输出决策指令。
"""

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.services.decision_engine import DecisionEngine
from src.services.feature_engine import FeatureEngine
from src.services.predictor import ModelPredictor
from src.services.risk_engine import RiskEngine
from src.services.shadow_ledger import ShadowLedger
from src.types.domain.decision import Decision
from src.types.domain.feature import ModelPrediction
from src.types.domain.quote import QuoteRaw
from src.types.enums.trading import HoldingPeriod

router = APIRouter(prefix="/decisions", tags=["decisions"])

_features = FeatureEngine()
_predictor = ModelPredictor()
_decision = DecisionEngine()
_risk = RiskEngine()
_ledger = ShadowLedger()


class DecisionEvaluateRequest(BaseModel):
    """决策触发请求参数。"""

    quote: QuoteRaw = Field(description="当前基准行情")
    target_buy_grams: float = Field(default=1.0, description="建议单笔申报克重")


class DecisionEvaluateResponse(BaseModel):
    """决策评估结果响应模型。"""

    raw_decision: Decision = Field(description="元决策引擎原始建议")
    approved_decision: Decision = Field(description="独立风控一票否决核验后的最终决策")
    risk_violations: list[str] = Field(default_factory=list, description="风控拦截违规原因列表")


@router.post("/evaluate", response_model=DecisionEvaluateResponse)
def evaluate_decision(payload: DecisionEvaluateRequest) -> DecisionEvaluateResponse:
    """对输入行情执行全流程特征工程、模型预测、元决策与风控核验。"""
    # 1. 提取特征
    feats = _features.extract_features([payload.quote])

    # 2. 预测
    pred = _predictor.predict(feats, horizon=HoldingPeriod.W1)
    preds: dict[HoldingPeriod, ModelPrediction] = {HoldingPeriod.W1: pred}

    # 3. 决策
    snap = _ledger.take_snapshot(payload.quote.quote_at or datetime.now(UTC))
    raw_dec = _decision.evaluate_decision(
        quote=payload.quote,
        predictions=preds,
        account=snap,
        target_buy_grams=payload.target_buy_grams,
    )

    # 4. 独立风控
    approved_dec, _, violations = _risk.check_decision(
        decision=raw_dec,
        account=snap,
        current_ask_price=payload.quote.ask_price,
        current_bid_price=payload.quote.bid_price,
    )

    return DecisionEvaluateResponse(
        raw_decision=raw_dec,
        approved_decision=approved_dec,
        risk_violations=violations,
    )
