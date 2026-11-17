"""决策接口数据传输模型。

定义决策触发评估与风控核验相关的接口请求与响应载荷模型。
"""

from pydantic import BaseModel, Field

from src.types.domain.decision import Decision
from src.types.domain.quote import QuoteRaw


class DecisionEvaluateRequest(BaseModel):
    """决策触发请求参数模型。"""

    quote: QuoteRaw = Field(description="当前基准行情")
    target_buy_grams: float = Field(default=1.0, description="建议单笔申报克重")


class DecisionEvaluateResponse(BaseModel):
    """决策评估结果响应模型。"""

    raw_decision: Decision = Field(description="元决策引擎原始建议")
    approved_decision: Decision = Field(description="独立风控一票否决核验后的最终决策")
    risk_violations: list[str] = Field(default_factory=list, description="风控拦截违规原因列表")
