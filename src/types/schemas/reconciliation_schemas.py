"""对账接口数据传输模型。

定义资产快照对账与订单流水对账相关的接口请求与响应载荷模型。
"""

from pydantic import BaseModel, Field

from src.types.domain.account import (
    BankStatementItem,
    OrderEvent,
    ReconciliationReport,
)
from src.types.enums.trading import RiskState


class SnapshotReconcileRequest(BaseModel):
    """资产快照对账请求载荷模型。"""

    bank_cash: float = Field(description="银行官方账户现金余额（元）")
    bank_gold_g: float = Field(description="银行官方黄金持仓总克重（克）")
    bank_cost_basis: float | None = Field(default=None, description="银行官方加权成本单价（元/克）")


class SnapshotReconcileResponse(BaseModel):
    """资产快照对账响应模型。"""

    report: ReconciliationReport = Field(description="对账检查报告")
    recommended_risk_state: RiskState = Field(description="推荐的风控状态（若不一致则为对账熔断停机）")


class OrdersReconcileRequest(BaseModel):
    """订单流水对账请求载荷模型。"""

    local_orders: list[OrderEvent] = Field(description="本地订单事件列表")
    bank_statements: list[BankStatementItem] = Field(description="银行官方交易流水记录")
