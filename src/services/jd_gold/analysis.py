"""京东黄金智能财富网关与分析方案业务服务。"""

from enum import Enum

from pydantic import JsonValue, PositiveInt

from src.clients.jd_gold import JDGoldClient
from src.contracts.enums.jd_gold import (
    Bank,
    FundRankSortBy,
    FundRankType,
    GoldRankType,
    IndexSymbol,
    InternationalSymbol,
    Symbol,
)


class JDGoldAnalysisService:
    """封装黄金分析智能网关指标、异动监测与基金/达人排行。"""

    def __init__(self, client: JDGoldClient) -> None:
        """初始化分析服务，注入底层 HTTP 客户端。"""
        self._client = client

    async def get_realtime_quote(
        self,
        unique_code: Bank | IndexSymbol | InternationalSymbol | Symbol | str,
    ) -> JsonValue:
        """查询分析网关实时行情。"""
        code = unique_code.value if isinstance(unique_code, Enum) else str(unique_code)
        return await self._client.gold_realtime_quote(code)

    async def get_bomb_indicators(self) -> JsonValue:
        """查询突发大单异动指标。"""
        return await self._client.gold_bomb()

    async def get_bomb_details(self) -> JsonValue:
        """查询突发大单异动明细。"""
        return await self._client.gold_bomb_details()

    async def get_opportunities(self) -> JsonValue:
        """查询黄金套利或波段机会分析。"""
        return await self._client.gold_opportunity()

    async def get_order_book(self) -> JsonValue:
        """查询黄金盘口挂单分布。"""
        return await self._client.gold_order_book()

    async def get_indicator_resonance(self) -> JsonValue:
        """查询多指标共振信号分析。"""
        return await self._client.gold_indicator_resonance()

    async def get_gold_ranking(self, rank_type: GoldRankType | str = GoldRankType.TOTAL_RETURN) -> JsonValue:
        """查询黄金达人与策略排行。"""
        rtype = rank_type.value if isinstance(rank_type, GoldRankType) else str(rank_type)
        return await self._client.gold_ranking(rank_type=rtype)

    async def get_fund_ranking(
        self,
        rank_type: FundRankType | PositiveInt = FundRankType.GOLD_ETF,
        rank_sort_by: FundRankSortBy | int = FundRankSortBy.DEFAULT,
        **params: JsonValue,
    ) -> JsonValue:
        """查询黄金基金多维排行。"""
        rtype = rank_type.value if isinstance(rank_type, FundRankType) else int(rank_type)
        sort_by = rank_sort_by.value if isinstance(rank_sort_by, FundRankSortBy) else int(rank_sort_by)
        return await self._client.fund_ranking(rank_type=rtype, rankSortBy=sort_by, **params)
