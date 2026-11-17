"""交易系统枚举定义。

涵盖交易方向、决策动作、风控状态机、数据质量状态、订单生命周期、提醒交互流程及回测验证分级。
"""

from enum import StrEnum


class TradeDirection(StrEnum):
    """交易方向。"""

    BUY = "BUY"
    SELL = "SELL"


class DecisionAction(StrEnum):
    """决策引擎输出动作。"""

    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    ABSTAIN = "ABSTAIN"


class RiskState(StrEnum):
    """风控状态机状态。

    流转方向：
        NORMAL → NO_NEW_RISK → REDUCE_ONLY → HALT_RECONCILE
        （反向恢复流转须经过显式授权与风控复核）
    """

    NORMAL = "NORMAL"
    NO_NEW_RISK = "NO_NEW_RISK"
    REDUCE_ONLY = "REDUCE_ONLY"
    HALT_RECONCILE = "HALT_RECONCILE"


class DataQualityState(StrEnum):
    """数据质量分类状态。"""

    NORMAL = "NORMAL"
    STALE = "STALE"
    GAP = "GAP"
    ANOMALY = "ANOMALY"


class OrderState(StrEnum):
    """订单生命周期状态。

    UNKNOWN 状态严禁盲目重试，必须先按唯一订单号查单确认。
    """

    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    UNKNOWN = "UNKNOWN"


class AlertState(StrEnum):
    """提醒与人工确认流程状态。"""

    INTENT_CREATED = "INTENT_CREATED"
    RISK_REJECTED = "RISK_REJECTED"
    ALERT_SENT = "ALERT_SENT"
    EXPIRED = "EXPIRED"
    USER_CONFIRMED = "USER_CONFIRMED"
    USER_DECLINED = "USER_DECLINED"
    USER_EXECUTED = "USER_EXECUTED"
    NOT_EXECUTED = "NOT_EXECUTED"
    RECONCILED = "RECONCILED"
    DISCREPANCY = "DISCREPANCY"


class GateResult(StrEnum):
    """阶段准入闸门结论。"""

    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"


class HoldingPeriod(StrEnum):
    """策略评估的候选持有周期。

    分钟级窗口仅用于数据节拍与风险监测，不作为独立开仓周期。
    """

    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    D3 = "3d"
    W1 = "1w"
    W2 = "2w"
    M1 = "1m"


class ValidationLevel(StrEnum):
    """回测结论验证分级。

    V0 结论一律标注「代理研究」。
    仅 V1–V3 结论方可使用「银行可成交」表述。
    """

    V0_PROXY = "V0"
    V1_BANK_REPLAY = "V1"
    V2_SHADOW = "V2"
    V3_LIVE_RECON = "V3"
