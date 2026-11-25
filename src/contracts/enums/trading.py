"""交易系统枚举定义。

涵盖交易方向、决策动作、风控状态机、数据质量状态、订单生命周期、提醒交互流程及回测验证分级。
所有枚举项均为 (value, description) 键值与中文描述对。
"""

from enum import StrEnum


class DescribedStrEnum(StrEnum):
    """带中文描述的字符串枚举基类。

    兼具字符串行为与中文描述属性，便于序列化、展示与业务核对。
    """

    description: str

    def __new__(cls, value: str, description: str = "") -> "DescribedStrEnum":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.description = description
        return obj

    @property
    def desc(self) -> str:
        """获取枚举项中文描述别名。"""
        return self.description

    @property
    def pair(self) -> tuple[str, str]:
        """获取 (value, description) 键值对元组。"""
        return (self.value, self.description)


class TradeDirection(DescribedStrEnum):
    """交易方向。"""

    BUY = ("BUY", "买入")
    SELL = ("SELL", "卖出")


class DecisionAction(DescribedStrEnum):
    """决策引擎输出动作。"""

    BUY = ("BUY", "买入")
    HOLD = ("HOLD", "观望持有")
    SELL = ("SELL", "卖出")
    ABSTAIN = ("ABSTAIN", "弃权不操作")


class RiskState(DescribedStrEnum):
    """风控状态机状态。

    流转方向：
        NORMAL → NO_NEW_RISK → REDUCE_ONLY → HALT_RECONCILE
        （反向恢复流转须经过显式授权与风控复核）
    """

    NORMAL = ("NORMAL", "正常运行")
    NO_NEW_RISK = ("NO_NEW_RISK", "禁止新增风险敞口")
    REDUCE_ONLY = ("REDUCE_ONLY", "仅允许平仓减仓")
    HALT_RECONCILE = ("HALT_RECONCILE", "对账差异紧急熔断停机")


class DataQualityState(DescribedStrEnum):
    """数据质量分类状态。"""

    NORMAL = ("NORMAL", "正常数据")
    STALE = ("STALE", "数据陈旧滞后")
    GAP = ("GAP", "行情时段缺失缺口")
    ANOMALY = ("ANOMALY", "异常跳变或畸变")


class OrderState(DescribedStrEnum):
    """订单生命周期状态。

    UNKNOWN 状态严禁盲目重试，必须先按唯一订单号查单确认。
    """

    CREATED = ("CREATED", "已创建未报送")
    SUBMITTED = ("SUBMITTED", "已报送待确认")
    ACKNOWLEDGED = ("ACKNOWLEDGED", "银行已受理接收")
    FILLED = ("FILLED", "全部成交完成")
    REJECTED = ("REJECTED", "委托被银行拒绝")
    CANCELED = ("CANCELED", "委托已成功撤销")
    UNKNOWN = ("UNKNOWN", "状态未知需查单核对")


class AlertState(DescribedStrEnum):
    """提醒与人工确认流程状态。"""

    INTENT_CREATED = ("INTENT_CREATED", "交易意向已生成")
    RISK_REJECTED = ("RISK_REJECTED", "被独立风控拒绝")
    ALERT_SENT = ("ALERT_SENT", "提醒已推送至用户")
    EXPIRED = ("EXPIRED", "提醒已超时作废")
    USER_CONFIRMED = ("USER_CONFIRMED", "用户已确认意向")
    USER_DECLINED = ("USER_DECLINED", "用户已拒绝建议")
    USER_EXECUTED = ("USER_EXECUTED", "用户已完成下单")
    NOT_EXECUTED = ("NOT_EXECUTED", "用户未在银行执行")
    RECONCILED = ("RECONCILED", "成交对账核对一致")
    DISCREPANCY = ("DISCREPANCY", "成交对账发现差异")


class GateResult(DescribedStrEnum):
    """阶段准入闸门结论。"""

    PASS = ("PASS", "准入闸门通过")
    FAIL = ("FAIL", "准入闸门未通过")
    PENDING = ("PENDING", "闸门等待核验中")


class HoldingPeriod(DescribedStrEnum):
    """策略评估的候选持有周期。

    分钟级窗口仅用于数据节拍与风险监测，不作为独立开仓周期。
    """

    H1 = ("1h", "1小时周期")
    H4 = ("4h", "4小时周期")
    D1 = ("1d", "1日周期")
    D3 = ("3d", "3日周期")
    W1 = ("1w", "1周周期")
    W2 = ("2w", "2周周期")
    M1 = ("1m", "1月周期")


class ValidationLevel(DescribedStrEnum):
    """回测结论验证分级。

    V0 结论一律标注「代理研究」。
    仅 V1–V3 结论方可使用「银行可成交」表述。
    """

    V0_PROXY = ("V0", "代理研究数据")
    V1_BANK_REPLAY = ("V1", "银行报价历史回放")
    V2_SHADOW = ("V2", "实时影子模拟运行")
    V3_LIVE_RECON = ("V3", "真实交易小额对账")


class RunMode(DescribedStrEnum):
    """主入口运行模式。"""

    BACKTEST = ("backtest", "历史回测")
    PAPER = ("paper", "模拟交易")
    LIVE = ("live", "实盘辅助")


class PaperEngine(DescribedStrEnum):
    """模拟交易执行引擎类型。"""

    LOCAL = ("local", "本地影子模拟盘")
    JD = ("jd", "京东黄金远程模拟盘")
