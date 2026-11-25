"""京东黄金行情与资讯业务方案服务。"""

from enum import Enum

from pydantic import JsonValue, PositiveInt

from src.clients.jd_gold import JDGoldClient
from src.contracts.enums.jd_gold import (
    Bank,
    GoldNewsQuery,
    IndexSymbol,
    InternationalSymbol,
    KLineType,
    NewsFlashTag,
    Symbol,
)


class JDGoldMarketService:
    """封装公开参考行情、现货报价与资讯检索业务。"""

    def __init__(self, client: JDGoldClient) -> None:
        """初始化行情服务，注入底层 HTTP 客户端。"""
        self._client = client

    async def get_price(self, bank: Bank | IndexSymbol | InternationalSymbol | str) -> JsonValue:
        """查询指定商业银行积存金或公开参考标的行情。"""
        code = bank.value if isinstance(bank, Enum) else str(bank)
        return await self._client.query_price(code)

    async def get_latest_price(self) -> dict[str, JsonValue]:
        """获取积存金最新参考报价原始信封。"""
        return await self._client.latest_price()

    async def get_stock_quote(self, symbol: Symbol | str) -> JsonValue:
        """查询上海黄金交易所贵金属现货行情。"""
        code = symbol.value if isinstance(symbol, Symbol) else str(symbol)
        return await self._client.stock_quote(code)

    async def get_stock_kline(
        self,
        symbol: Symbol | str,
        k_type: KLineType | str = KLineType.DAY,
    ) -> JsonValue:
        """查询贵金属 K 线图表数据。"""
        code = symbol.value if isinstance(symbol, Symbol) else str(symbol)
        ktype = k_type.value if isinstance(k_type, KLineType) else str(k_type)
        return await self._client.stock_kline(code, k_type=ktype)

    async def get_news(
        self,
        query: GoldNewsQuery | str = GoldNewsQuery.GOLD,
        size: PositiveInt = 5,
    ) -> JsonValue:
        """检索黄金资讯列表。"""
        q = query.value if isinstance(query, GoldNewsQuery) else str(query)
        return await self._client.news(query=q, size=size)

    async def get_news_flash(
        self,
        tag_id: NewsFlashTag | PositiveInt = NewsFlashTag.GOLD,
        page_size: PositiveInt = 10,
        last_id: str = "",
    ) -> JsonValue:
        """查询黄金快讯列表。"""
        tag_val = tag_id.value if isinstance(tag_id, NewsFlashTag) else tag_id
        return await self._client.news_flash(tag_id=tag_val, page_size=page_size, last_id=last_id)

    async def get_morning_report(self, page_size: PositiveInt = 20) -> JsonValue:
        """查询黄金早报列表。"""
        return await self._client.morning_report(page_size=page_size)
