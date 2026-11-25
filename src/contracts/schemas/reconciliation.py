"""对账接口数据传输模型。

定义资产快照对账与订单流水对账相关的接口请求与响应载荷模型。
"""

from pydantic import AwareDatetime, BaseModel, Field, FiniteFloat, PositiveFloat

from src.contracts.domains.account import (
    BankStatementItem,
    OrderEvent,
    ReconciliationReport,
)
from src.contracts.enums.trading import RiskState


class ReconcileOrder(OrderEvent):
    """约束外部订单流水中的数量、费用与时间。"""

    requested_grams: PositiveFloat = Field(allow_inf_nan=False)
    filled_grams: FiniteFloat | None = Field(default=None, ge=0)
    limit_price: PositiveFloat | None = Field(default=None, allow_inf_nan=False)
    batch_price: PositiveFloat | None = Field(default=None, allow_inf_nan=False)
    execution_price: PositiveFloat | None = Field(default=None, allow_inf_nan=False)
    fee: FiniteFloat | None = Field(default=None, ge=0)
    bank_event_at: AwareDatetime | None = None
    received_at: AwareDatetime | None = None


class ReconcileStatement(BankStatementItem):
    """约束外部银行成交记录。"""

    grams: PositiveFloat = Field(allow_inf_nan=False)
    price: PositiveFloat = Field(allow_inf_nan=False)
    fee: FiniteFloat = Field(default=0, ge=0)
    executed_at: AwareDatetime


class SnapshotReconcileRequest(BaseModel):
    """资产快照对账请求载荷模型。"""

    bank_cash: FiniteFloat = Field(ge=0, description="银行官方账户现金余额（元）")
    bank_gold_g: FiniteFloat = Field(ge=0, description="银行官方黄金持仓总克重（克）")
    bank_cost_basis: PositiveFloat | None = Field(
        default=None, allow_inf_nan=False, description="银行官方加权成本单价（元/克）"
    )


class SnapshotReconcileResponse(BaseModel):
    """资产快照对账响应模型。"""

    report: ReconciliationReport = Field(description="对账检查报告")
    recommended_risk_state: RiskState = Field(description="推荐的风控状态（若不一致则为对账熔断停机）")


class OrdersReconcileRequest(BaseModel):
    """订单流水对账请求载荷模型。"""

    local_orders: list[ReconcileOrder] = Field(description="本地订单事件列表")
    bank_statements: list[ReconcileStatement] = Field(description="银行官方交易流水记录")
