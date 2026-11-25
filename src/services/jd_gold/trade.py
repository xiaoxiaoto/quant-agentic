"""京东黄金持仓、真实订单与模拟交易业务方案服务。"""

from enum import Enum

from pydantic import JsonValue, NonNegativeInt, PositiveFloat, PositiveInt

from src.clients.jd_gold import JDGoldClient
from src.contracts.enums.jd_gold import (
    Bank,
    ConditionalOrderStatus,
    HoldingBank,
    IncomeCalendarTimeType,
    IncomeCalendarTradeType,
    IndexSymbol,
    KLineAdjustType,
    KLineType,
    OrderBusiness,
    OrderChannel,
    OrderStatus,
    OrderTradeType,
    SimAccountType,
    SimTradeUnit,
    Symbol,
    TimeSharingType,
)


class JDGoldTradeService:
    """封装持仓查询、订单流水管理与显式模拟盘交易。"""

    def __init__(self, client: JDGoldClient) -> None:
        """初始化交易服务，注入底层 HTTP 客户端。"""
        self._client = client

    def _extract_bank_code(self, bank: Bank | HoldingBank | str) -> str:
        """从积存金枚举或代码字符串提取商业银行简短代码标识。"""
        if isinstance(bank, Bank):
            return bank.bank_code
        if isinstance(bank, HoldingBank):
            return bank.value
        bank_str = str(bank)
        return bank_str.split("-")[0] if "-" in bank_str else bank_str

    async def get_holdings(self) -> JsonValue:
        """查询真实账户黄金持仓明细。"""
        return await self._client.holdings()

    async def list_orders(
        self,
        order_create_start_date: str,
        order_create_end_date: str,
        *,
        page_no: PositiveInt = 1,
        page_size: PositiveInt = 50,
        business: OrderBusiness | str = "",
        status_list: list[OrderStatus | str] | None = None,
        trade_type_list: list[OrderTradeType | str] | None = None,
        channel_ids: list[OrderChannel | str] | None = None,
        **params: JsonValue,
    ) -> JsonValue:
        """查询历史订单交易记录。"""
        b_code = business.value if isinstance(business, OrderBusiness) else str(business)
        statuses: list[JsonValue] = (
            [s.value if isinstance(s, OrderStatus) else str(s) for s in status_list] if status_list else []
        )
        trades: list[JsonValue] = (
            [t.value if isinstance(t, OrderTradeType) else str(t) for t in trade_type_list] if trade_type_list else []
        )
        channels: list[JsonValue] = (
            [c.value if isinstance(c, OrderChannel) else str(c) for c in channel_ids] if channel_ids else []
        )
        return await self._client.order_list(
            order_create_start_date,
            order_create_end_date,
            pageNo=page_no,
            pageSize=page_size,
            businessCode=b_code,
            statusList=statuses,
            tradeTypeCodeList=trades,
            bizChannelIds=channels,
            **params,
        )

    async def get_order_summary(
        self,
        order_create_start_date: str,
        order_create_end_date: str,
        **params: JsonValue,
    ) -> JsonValue:
        """查询指定时间段内的订单汇总统计。"""
        return await self._client.order_sum(order_create_start_date, order_create_end_date, **params)

    async def list_conditional_orders(
        self,
        bank: Bank | HoldingBank | str,
        start_time: NonNegativeInt,
        end_time: NonNegativeInt,
        *,
        jrid: str = "",
        status_list: list[ConditionalOrderStatus | str] | None = None,
        page_index: PositiveInt = 1,
        page_size: PositiveInt = 10,
        **params: JsonValue,
    ) -> JsonValue:
        """查询条件挂单列表。"""
        bank_code = self._extract_bank_code(bank)
        statuses: list[JsonValue] = (
            [s.value if isinstance(s, ConditionalOrderStatus) else str(s) for s in status_list]
            if status_list is not None
            else ["1"]
        )
        return await self._client.conditional_orders(
            bank_code,
            start_time,
            end_time,
            jrid=jrid,
            statusList=statuses,
            pageIndex=page_index,
            pageSize=page_size,
            **params,
        )

    async def get_conditional_order_detail(
        self,
        bank: Bank | HoldingBank | str,
        conditional_uuid: str,
        *,
        jrid: str = "",
    ) -> JsonValue:
        """查询指定条件挂单详情。"""
        bank_code = self._extract_bank_code(bank)
        return await self._client.conditional_order_detail(bank_code, conditional_uuid, jrid=jrid)

    async def get_income_calendar(
        self,
        time_type: IncomeCalendarTimeType | str = IncomeCalendarTimeType.DAY,
        time: str = "",
        trade_type: IncomeCalendarTradeType | str = IncomeCalendarTradeType.ALL,
        **params: JsonValue,
    ) -> JsonValue:
        """查询收益日历数据。"""
        ttype = time_type.value if isinstance(time_type, IncomeCalendarTimeType) else str(time_type)
        trtype = trade_type.value if isinstance(trade_type, IncomeCalendarTradeType) else str(trade_type)
        return await self._client.income_calendar(ttype, time, trade_type=trtype, **params)

    async def get_or_init_sim_account(
        self,
        account_type: SimAccountType | PositiveInt = SimAccountType.DEFAULT,
    ) -> JsonValue:
        """查询并初始化显式模拟账户。"""
        act_type = account_type.value if isinstance(account_type, SimAccountType) else int(account_type)
        return await self._client.sim_account(account_type=act_type)

    async def execute_sim_buy(
        self,
        trade_unit: SimTradeUnit | PositiveInt,
        bus_id: HoldingBank | Bank | str = HoldingBank.CZB,
        *,
        trade_amount: str | PositiveInt | PositiveFloat | None = None,
        trade_gram: str | PositiveInt | PositiveFloat | None = None,
        account_type: SimAccountType | PositiveInt = SimAccountType.DEFAULT,
    ) -> JsonValue:
        """执行模拟买入交易。"""
        t_unit = trade_unit.value if isinstance(trade_unit, SimTradeUnit) else int(trade_unit)
        b_id = self._extract_bank_code(bus_id)
        act_type = account_type.value if isinstance(account_type, SimAccountType) else int(account_type)
        return await self._client.sim_buy(
            t_unit,
            b_id,
            trade_amount=trade_amount,
            trade_gram=trade_gram,
            account_type=act_type,
        )

    async def execute_sim_sell(
        self,
        trade_unit: SimTradeUnit | PositiveInt,
        bus_id: HoldingBank | Bank | str = HoldingBank.CZB,
        *,
        trade_gram: str | PositiveInt | PositiveFloat | None = None,
        trade_ratio: str | PositiveInt | PositiveFloat | None = None,
        account_type: SimAccountType | PositiveInt = SimAccountType.DEFAULT,
    ) -> JsonValue:
        """执行模拟卖出交易。"""
        t_unit = trade_unit.value if isinstance(trade_unit, SimTradeUnit) else int(trade_unit)
        b_id = self._extract_bank_code(bus_id)
        act_type = account_type.value if isinstance(account_type, SimAccountType) else int(account_type)
        return await self._client.sim_sell(
            t_unit,
            b_id,
            trade_gram=trade_gram,
            trade_ratio=trade_ratio,
            account_type=act_type,
        )

    async def get_sim_trade_overview(
        self,
        account_type: SimAccountType | PositiveInt = SimAccountType.DEFAULT,
    ) -> JsonValue:
        """查询模拟交易概览。"""
        act_type = account_type.value if isinstance(account_type, SimAccountType) else int(account_type)
        return await self._client.sim_trade_overview(account_type=act_type)

    async def list_sim_trade_records(
        self,
        last_id: str | None = None,
        page_size: PositiveInt = 8,
        account_type: SimAccountType | PositiveInt = SimAccountType.DEFAULT,
    ) -> JsonValue:
        """分页查询模拟交易记录流水。"""
        act_type = account_type.value if isinstance(account_type, SimAccountType) else int(account_type)
        return await self._client.sim_trade_records(last_id=last_id, page_size=page_size, account_type=act_type)

    async def get_sim_time_sharing(
        self,
        unique_code: IndexSymbol | Bank | Symbol | str,
        type_: TimeSharingType | str = TimeSharingType.M1,
        **params: JsonValue,
    ) -> JsonValue:
        """查询模拟分时走势。"""
        code = unique_code.value if isinstance(unique_code, Enum) else str(unique_code)
        ttype = type_.value if isinstance(type_, TimeSharingType) else str(type_)
        return await self._client.sim_time_sharing(code, type_=ttype, **params)

    async def get_sim_kline(
        self,
        unique_code: IndexSymbol | Bank | Symbol | str,
        k_type: KLineType | str = KLineType.DAY,
        af_type: KLineAdjustType | str = KLineAdjustType.BFQ,
        **params: JsonValue,
    ) -> JsonValue:
        """查询模拟 K 线图表数据。"""
        code = unique_code.value if isinstance(unique_code, Enum) else str(unique_code)
        ktype = k_type.value if isinstance(k_type, KLineType) else str(k_type)
        aftype = af_type.value if isinstance(af_type, KLineAdjustType) else str(af_type)
        return await self._client.sim_kline(code, k_type=ktype, af_type=aftype, **params)
