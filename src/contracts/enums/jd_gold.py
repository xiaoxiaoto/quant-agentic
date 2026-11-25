"""京东黄金业务契约枚举定义。

集中声明商业银行积存金代码、上金所标的代码、指数及外盘品种、K 线周期与复权、
分时颗粒度、达人及基金排行榜维度、模拟交易计量与账户类型、收益日历维度、挂单与订单状态、资讯与智能分析指标。
"""

from enum import IntEnum

from src.contracts.enums.trading import DescribedStrEnum


class DescribedIntEnum(IntEnum):
    """带中文描述的整型枚举基类。"""

    description: str

    def __new__(cls, value: int, description: str = "") -> "DescribedIntEnum":
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.description = description
        return obj

    @property
    def desc(self) -> str:
        """获取枚举项中文描述别名。"""
        return self.description

    @property
    def pair(self) -> tuple[int, str]:
        """获取 (value, description) 键值对元组。"""
        return (self.value, self.description)


class Bank(DescribedStrEnum):
    """商业银行积存金品种代码枚举。"""

    CMBC = ("CMBC-JCJ", "民生银行")
    CZB = ("CZB-JCJ", "浙商银行")
    ICBC = ("ICBC-JCJ", "工商银行")
    CIB = ("CIB-JCJ0", "兴业银行")
    CGB = ("CGB-JCJ0", "广发银行")
    CNCB = ("CNCB-JCJ", "中信银行")
    SPDB = ("SPDB-JCJ0", "浦发银行")

    @property
    def bank_code(self) -> str:
        """获取商业银行简短英文字母标识 (例如 CMBC-JCJ -> CMBC)。"""
        return self.value.split("-")[0]

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (中文名称, 标的代码) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部品种的 (中文名称, 标的代码) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class HoldingBank(DescribedStrEnum):
    """商业银行持仓与挂单简短代码枚举。"""

    CMBC = ("CMBC", "民生银行")
    CZB = ("CZB", "浙商银行")
    ICBC = ("ICBC", "工商银行")
    CIB = ("CIB", "兴业银行")
    CGB = ("CGB", "广发银行")
    CNCB = ("CNCB", "中信银行")
    SPDB = ("SPDB", "浦发银行")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (中文名称, 银行短码) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部银行简短代码的 (中文名称, 银行短码) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class Symbol(DescribedStrEnum):
    """上海黄金交易所官方场内现货与延期标的代码枚举。"""

    AU9999 = ("SGE-Au99.99", "黄金9999")
    AUTD = ("SGE-Au(T+D)", "黄金T+D")
    AU100G = ("SGE-Au100g", "100克金条")
    AGTD = ("SGE-Ag(T+D)", "白银延期")
    PT9995 = ("SGE-Pt99.95", "现货铂金")
    MAUTD = ("SGE-mAu(T+D)", "迷你黄金延期")
    AU9995 = ("SGE-Au99.95", "黄金9995")
    IAU9999 = ("SGE-iAu99.99", "国际版黄金9999")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (中文名称, 标的代码) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部品种的 (中文名称, 标的代码) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class IndexSymbol(DescribedStrEnum):
    """京东金价指数标的代码枚举。"""

    JDAU = ("WG-JDAU", "京东金价指数")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (中文名称, 标的代码) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部指数标的的 (中文名称, 标的代码) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class InternationalSymbol(DescribedStrEnum):
    """国际外盘贵金属现货标的代码枚举。"""

    XAUUSD = ("WG-XAUUSD", "伦敦金现货")
    XAGUSD = ("WG-XAGUSD", "伦敦银现货")
    XPTUSD = ("WG-XPTUSD", "国际现货铂金")
    XPDUSD = ("WG-XPDUSD", "国际现货钯金")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (中文名称, 标的代码) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部外盘标的的 (中文名称, 标的代码) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class KLineType(DescribedStrEnum):
    """K 线周期类型枚举。"""

    DAY = ("day", "日K")
    WEEK = ("week", "周K")
    MONTH = ("month", "月K")
    YEAR = ("year", "年K")
    M1 = ("m1", "1分钟K")
    M5 = ("m5", "5分钟K")
    M15 = ("m15", "15分钟K")
    M30 = ("m30", "30分钟K")
    M60 = ("m60", "60分钟K")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (周期描述, 周期代码) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部 K 线周期的 (中文描述, 周期代码) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class KLineAdjustType(DescribedStrEnum):
    """K 线价格复权方式枚举。"""

    BFQ = ("bfq", "不复权")
    QFQ = ("qfq", "前复权")
    HFQ = ("hfq", "后复权")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (复权描述, 标识) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部复权方式的 (中文描述, 标识) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class TimeSharingType(DescribedStrEnum):
    """模拟分时图时间颗粒度枚举。"""

    M1 = ("m1", "1分钟分时")
    M5 = ("m5", "5分钟分时")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (颗粒度描述, 标识) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部分时周期的 (中文描述, 标识) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class GoldRankType(DescribedStrEnum):
    """黄金达人与定投策略排行维度枚举。"""

    TOTAL_RETURN = ("1", "累计收益榜")
    WEEKLY_RETURN = ("2", "周收益榜")
    MONTHLY_RETURN = ("3", "月收益榜")
    WIN_RATE = ("4", "稳健胜率榜")
    POPULARITY = ("5", "人气跟投榜")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (榜单描述, 榜单代码) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部达人榜单维度的 (中文描述, 榜单代码) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class FundRankType(DescribedIntEnum):
    """黄金主题公募基金多维排行维度枚举。"""

    GOLD_ETF = (401, "黄金ETF基金排行")
    GOLD_LINK = (402, "黄金联接基金排行")
    MARKET_ALL = (101, "全市场基金收益榜")

    @property
    def label_pair(self) -> tuple[str, int]:
        """获取 (榜单描述, 榜单代码) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, int]]:
        """获取全部基金榜单维度的 (中文描述, 榜单代码) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class FundRankSortBy(DescribedIntEnum):
    """黄金基金多维排行排序维度枚举。"""

    DEFAULT = (0, "综合排序")
    RETURN_RATE = (1, "收益率降序")
    POPULARITY = (2, "人气关注降序")

    @property
    def label_pair(self) -> tuple[str, int]:
        """获取 (排序维度描述, 标识数值) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, int]]:
        """获取全部排序维度的 (中文描述, 标识数值) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class SimAccountType(DescribedIntEnum):
    """模拟交易账户分类枚举。"""

    DEFAULT = (1, "标准模拟账户")

    @property
    def label_pair(self) -> tuple[str, int]:
        """获取 (账户类型描述, 类型数值) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, int]]:
        """获取全部模拟账户类型的 (中文描述, 类型数值) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class SimTradeUnit(DescribedIntEnum):
    """模拟交易委托计量单位枚举。"""

    GRAM = (1, "按克重委托")
    AMOUNT = (2, "按金额委托")
    RATIO = (3, "按比例委托")

    @property
    def label_pair(self) -> tuple[str, int]:
        """获取 (计量单位描述, 标识数值) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, int]]:
        """获取全部委托计量单位的 (中文描述, 标识数值) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class IncomeCalendarTimeType(DescribedStrEnum):
    """收益日历时间聚合统计周期枚举。"""

    DAY = ("day", "按日统计")
    MONTH = ("month", "按月统计")
    YEAR = ("year", "按年统计")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (统计周期描述, 标识) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部时间聚合维度的 (中文描述, 标识) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class IncomeCalendarTradeType(DescribedStrEnum):
    """收益日历交易方向筛选枚举。"""

    ALL = ("1", "全部交易方向")
    BUY = ("2", "仅买入交易")
    SELL = ("3", "仅卖出交易")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (交易类型描述, 标识) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部交易类型的 (中文描述, 标识) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class ConditionalOrderStatus(DescribedStrEnum):
    """条件挂单状态枚举。"""

    PENDING = ("1", "有效生效中")
    TRIGGERED = ("2", "已触发执行")
    CANCELLED = ("3", "已撤销终止")
    EXPIRED = ("4", "已过期失效")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (状态描述, 状态标识) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部挂单状态的 (中文描述, 状态标识) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class OrderStatus(DescribedStrEnum):
    """黄金真实订单状态枚举。"""

    SUCCESS = ("SUCCESS", "交易成功")
    FAIL = ("FAIL", "交易失败")
    PROCESSING = ("PROCESSING", "处理中")
    CANCELLED = ("CANCELLED", "已取消")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (状态描述, 状态代码) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部订单状态的 (中文描述, 状态代码) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class OrderTradeType(DescribedStrEnum):
    """黄金真实订单交易类型枚举。"""

    BUY = ("BUY", "买入")
    SELL = ("SELL", "卖出")
    TRANSFER = ("TRANSFER", "转让")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (交易类型描述, 类型代码) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部交易类型的 (中文描述, 类型代码) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class OrderChannel(DescribedStrEnum):
    """黄金业务下单渠道枚举。"""

    APP = ("APP", "京东APP端")
    H5 = ("H5", "移动端H5")
    PC = ("PC", "电脑端网页")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (渠道描述, 渠道标识) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部下单渠道的 (中文描述, 渠道标识) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class OrderBusiness(DescribedStrEnum):
    """黄金业务模式枚举。"""

    GOLD_REGULAR = ("GOLD_REGULAR", "积存金定投")
    GOLD_SPOT = ("GOLD_SPOT", "黄金现货买卖")
    GOLD_SIM = ("GOLD_SIM", "模拟盘交易")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (业务模式描述, 模式代码) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部业务模式的 (中文描述, 模式代码) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class NewsFlashTag(DescribedIntEnum):
    """黄金快讯资讯分类标签枚举。"""

    GOLD = (20225, "黄金专区快讯")

    @property
    def label_pair(self) -> tuple[str, int]:
        """获取 (标签描述, 标签数值) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, int]]:
        """获取全部快讯标签的 (中文描述, 标签数值) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class GoldNewsQuery(DescribedStrEnum):
    """黄金新闻资讯常用检索词枚举。"""

    GOLD = ("黄金", "黄金资讯")
    SILVER = ("白银", "白银资讯")
    PRECIOUS_METAL = ("贵金属", "贵金属综合")
    CENTRAL_BANK = ("央行购金", "央行储备")
    ETF = ("黄金ETF", "基金与ETF")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (检索词描述, 检索关键词) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部常用检索词的 (中文描述, 检索关键词) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class AnalysisIndicatorType(DescribedStrEnum):
    """智能分析网关指标能力端点枚举。"""

    REALTIME_QUOTE = ("realtime-quote", "网关实时行情")
    BOMB = ("bomb", "突发大单异动雷达")
    BOMB_DETAILS = ("bomb-details", "突发大单异动明细")
    OPPORTUNITY = ("opportunity", "套利与波段机会分析")
    ORDER_BOOK = ("order-book", "盘口挂单分布")
    INDICATOR_RESONANCE = ("indicator-resonance", "多指标共振信号分析")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (指标能力描述, 接口端点标识) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部指标能力的 (中文描述, 接口端点标识) 元组列表。"""
        return [(item.description, item.value) for item in cls]


class AnalysisSkill(DescribedStrEnum):
    """智能分析网关技能标识枚举。"""

    GOLD_ANALYSIS_HUB = ("gold-analysis-hub", "黄金智能分析网关")
    BLOGGER_TREND = ("blogger-trend", "达人与基金趋势排行")

    @property
    def label_pair(self) -> tuple[str, str]:
        """获取 (技能描述, 技能标识) 元组。"""
        return (self.description, self.value)

    @classmethod
    def as_tuples(cls) -> list[tuple[str, str]]:
        """获取全部技能标识的 (中文描述, 技能标识) 元组列表。"""
        return [(item.description, item.value) for item in cls]
