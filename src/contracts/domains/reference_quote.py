"""与可成交双边报价隔离的单边参考行情。"""

from typing import ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, Field, FiniteFloat

from src.contracts.enums.trading import DataQualityState


class ReferenceProduct(BaseModel):
    """参考行情品种的固定身份、原始报价单位和价格方向。"""

    code: str
    name: str
    unit: Literal["CNY/g", "CNY/kg", "USD/oz"] = "CNY/g"
    category: Literal["bank", "index", "international", "sge"]
    price_side: Literal["reference", "buy"] = "reference"


class ReferenceProductCatalog:
    """集中声明银行积存金、京东指数与境内外贵金属行情。"""

    # 品种编码到对象的 O(1) 查找索引，首次访问时惰性初始化。
    _INDEX: ClassVar[dict[str, ReferenceProduct]] = {}

    PRODUCTS: ClassVar[tuple[ReferenceProduct, ...]] = (
        ReferenceProduct(code="CMBC-JCJ", name="民生银行积存金", category="bank"),
        ReferenceProduct(code="CIB-JCJ0", name="兴业银行积存金", category="bank", price_side="buy"),
        ReferenceProduct(code="CGB-JCJ0", name="广发银行积存金", category="bank", price_side="buy"),
        ReferenceProduct(code="CZB-JCJ", name="浙商银行积存金", category="bank"),
        ReferenceProduct(code="ICBC-JCJ", name="工商银行积存金", category="bank"),
        ReferenceProduct(code="CNCB-JCJ", name="中信银行积存金", category="bank"),
        # ReferenceProduct(code="SPDB-JCJ0", name="浦发银行积存金", category="bank", price_side="buy"),
        ReferenceProduct(code="WG-JDAU", name="京东金价指数", category="index"),
        ReferenceProduct(code="WG-XAUUSD", name="伦敦金", unit="USD/oz", category="international"),
        ReferenceProduct(code="WG-XAGUSD", name="伦敦银", unit="USD/oz", category="international"),
        ReferenceProduct(code="WG-XPTUSD", name="现货铂金", unit="USD/oz", category="international"),
        ReferenceProduct(code="WG-XPDUSD", name="现货钯金", unit="USD/oz", category="international"),
        ReferenceProduct(code="SGE-Au99.99", name="黄金9999", category="sge"),
        ReferenceProduct(code="SGE-Au(T+D)", name="黄金延期", category="sge"),
        ReferenceProduct(code="SGE-mAu(T+D)", name="迷你黄金延期", category="sge"),
        ReferenceProduct(code="SGE-Ag(T+D)", name="白银延期", unit="CNY/kg", category="sge"),
        ReferenceProduct(code="SGE-Au99.95", name="黄金9995", category="sge"),
        ReferenceProduct(code="SGE-Au100g", name="黄金100克", category="sge"),
        ReferenceProduct(code="SGE-Pt99.95", name="铂金9995", category="sge"),
        ReferenceProduct(code="SGE-Au(T+N1)", name="黄金延期N1", category="sge"),
        ReferenceProduct(code="SGE-Au(T+N2)", name="黄金延期N2", category="sge"),
        ReferenceProduct(code="SGE-Ag99.99", name="白银9999", unit="CNY/kg", category="sge"),
        ReferenceProduct(code="SGE-iAu99.99", name="国际版黄金9999", category="sge"),
        ReferenceProduct(code="SGE-Au99.5", name="黄金995", category="sge"),
        ReferenceProduct(code="SGE-iAu100g", name="国际版黄金100克", category="sge"),
        ReferenceProduct(code="SGE-iAu99.5", name="国际版黄金995", category="sge"),
        ReferenceProduct(code="SGE-PGC30g", name="熊猫金币30克", category="sge"),
        ReferenceProduct(code="SGE-NYAuTN06", name="纽约金TN06", category="sge"),
        ReferenceProduct(code="SGE-NYAuTN12", name="纽约金TN12", category="sge"),
    )

    @classmethod
    def get(cls, code: str) -> ReferenceProduct:
        """按固定编码获取品种，未知产品明确拒绝。"""
        if not cls._INDEX:
            cls._INDEX = {p.code: p for p in cls.PRODUCTS}
        try:
            return cls._INDEX[code]
        except KeyError:
            raise ValueError("不支持的参考行情品种") from None

    @classmethod
    def all(cls) -> tuple[ReferenceProduct, ...]:
        """返回全部支持品种及其原始报价口径。"""
        return cls.PRODUCTS


class ReferenceQuote(BaseModel):
    """保留原始单位的参考价；缺少源时间时为空，不能进入交易决策。"""

    quote_id: str
    source_id: str = "JD_CMBC_REFERENCE"
    product_code: str = "CMBC-JCJ"
    unit: Literal["CNY/g", "CNY/kg", "USD/oz"] = "CNY/g"
    price_side: Literal["reference", "buy"] = "reference"
    price: FiniteFloat = Field(gt=0)
    quote_at: AwareDatetime | None
    received_at: AwareDatetime
    available_at: AwareDatetime | None = None
    is_tradable: Literal[False] = False
    quality_state: DataQualityState = DataQualityState.NORMAL
    payload_hash: str
    record_hash: str | None = None


class LiveQuoteStatus(BaseModel):
    """采集进程状态与最近结果；不包含鉴权配置。"""

    enabled: bool
    running: bool
    interval_seconds: float
    records_saved: int
    failures: int
    last_attempt_at: AwareDatetime | None
    last_success_at: AwareDatetime | None
    last_error: str | None
    latest: ReferenceQuote | None
    product_code: str = "CMBC-JCJ"
    cny_per_g: float | None = None
    fx_rate_usd_cny: float | None = None
