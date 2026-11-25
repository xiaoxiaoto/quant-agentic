"""决策接口数据传输模型。

定义决策触发评估与风控核验相关的接口请求与响应载荷模型。
"""

from pydantic import AwareDatetime, BaseModel, Field, PositiveFloat, model_validator

from src.contracts.domains.decision import Decision
from src.contracts.domains.feature import ModelPrediction
from src.contracts.domains.quote import QuoteRaw


class DecisionQuote(QuoteRaw):
    """参与计算的行情必须使用有限正价格、非倒挂点差和带时区时间。"""

    bid_price: PositiveFloat = Field(allow_inf_nan=False)
    ask_price: PositiveFloat = Field(allow_inf_nan=False)
    quote_at: AwareDatetime
    received_at: AwareDatetime
    available_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _validate_spread(self) -> "DecisionQuote":
        """禁止买卖价倒挂进入决策评估。"""
        if self.ask_price < self.bid_price:
            raise ValueError("买入价（ask_price）不得低于卖出价（bid_price）")
        return self


class DecisionEvaluateRequest(BaseModel):
    """决策触发请求参数模型。"""

    quote: DecisionQuote = Field(description="当前基准行情")
    target_buy_grams: PositiveFloat = Field(default=1.0, allow_inf_nan=False, description="建议单笔申报克重")


class DecisionEvaluateResponse(BaseModel):
    """决策评估结果响应模型。"""

    raw_decision: Decision = Field(description="元决策引擎原始建议")
    approved_decision: Decision = Field(description="独立风控一票否决核验后的最终决策")
    risk_violations: list[str] = Field(default_factory=list, description="风控拦截违规原因列表")
    prediction: ModelPrediction | None = Field(default=None, description="实际模型输出及校准状态")
