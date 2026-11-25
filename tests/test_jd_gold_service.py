"""京东黄金业务子服务（授权、行情、交易、分析方案）单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from pydantic import SecretStr

from src.clients.jd_gold import JDGoldClient
from src.configs.settings import Settings
from src.contracts.domains.jd_auth import JDGoldAuthState
from src.contracts.enums.jd_gold import (
    AnalysisIndicatorType,
    AnalysisSkill,
    Bank,
    ConditionalOrderStatus,
    FundRankSortBy,
    FundRankType,
    GoldNewsQuery,
    GoldRankType,
    HoldingBank,
    IncomeCalendarTimeType,
    IncomeCalendarTradeType,
    IndexSymbol,
    InternationalSymbol,
    KLineAdjustType,
    KLineType,
    NewsFlashTag,
    OrderBusiness,
    OrderChannel,
    OrderStatus,
    OrderTradeType,
    SimAccountType,
    SimTradeUnit,
    Symbol,
    TimeSharingType,
)
from src.repositories.jd_gold_auth import JDGoldAuthRepository
from src.services.jd_gold.analysis import JDGoldAnalysisService
from src.services.jd_gold.auth import JDGoldAuthService
from src.services.jd_gold.gateway import JDGoldGateway
from src.services.jd_gold.market import JDGoldMarketService
from src.services.jd_gold.trade import JDGoldTradeService


def test_market_service_enum_definitions() -> None:
    # 银行积存金
    assert Bank.CMBC.value == "CMBC-JCJ"
    assert Bank.CMBC.desc == "民生银行"
    assert Bank.CMBC.bank_code == "CMBC"
    assert Bank.CZB.value == "CZB-JCJ"
    assert Bank.ICBC.value == "ICBC-JCJ"
    assert Bank.CIB.value == "CIB-JCJ0"
    assert Bank.CGB.value == "CGB-JCJ0"
    assert Bank.CNCB.value == "CNCB-JCJ"
    assert Bank.SPDB.value == "SPDB-JCJ0"
    assert ("民生银行", "CMBC-JCJ") in Bank.as_tuples()
    assert ("浦发银行", "SPDB-JCJ0") in Bank.as_tuples()

    # 银行短码枚举
    assert HoldingBank.CMBC.value == "CMBC"
    assert ("民生银行", "CMBC") in HoldingBank.as_tuples()

    # 上金所标的
    assert Symbol.AU9999.value == "SGE-Au99.99"
    assert Symbol.AU9999.desc == "黄金9999"
    assert Symbol.AUTD.value == "SGE-Au(T+D)"
    assert Symbol.AU100G.value == "SGE-Au100g"
    assert Symbol.AGTD.value == "SGE-Ag(T+D)"
    assert Symbol.PT9995.value == "SGE-Pt99.95"
    assert Symbol.MAUTD.value == "SGE-mAu(T+D)"
    assert Symbol.AU9995.value == "SGE-Au99.95"
    assert Symbol.IAU9999.value == "SGE-iAu99.99"
    assert ("黄金9999", "SGE-Au99.99") in Symbol.as_tuples()
    assert ("国际版黄金9999", "SGE-iAu99.99") in Symbol.as_tuples()

    # 指数与外盘标的
    assert IndexSymbol.JDAU.value == "WG-JDAU"
    assert ("京东金价指数", "WG-JDAU") in IndexSymbol.as_tuples()
    assert InternationalSymbol.XAUUSD.value == "WG-XAUUSD"
    assert InternationalSymbol.XAUUSD.desc == "伦敦金现货"
    assert ("伦敦金现货", "WG-XAUUSD") in InternationalSymbol.as_tuples()

    # K 线与分时
    assert KLineType.DAY.value == "day"
    assert KLineType.DAY.desc == "日K"
    assert KLineType.WEEK.value == "week"
    assert KLineType.MONTH.value == "month"
    assert KLineType.YEAR.value == "year"
    assert KLineType.M1.value == "m1"
    assert KLineType.M5.value == "m5"
    assert KLineType.M15.value == "m15"
    assert KLineType.M30.value == "m30"
    assert KLineType.M60.value == "m60"
    assert ("日K", "day") in KLineType.as_tuples()

    assert KLineAdjustType.BFQ.value == "bfq"
    assert KLineAdjustType.QFQ.value == "qfq"
    assert ("不复权", "bfq") in KLineAdjustType.as_tuples()

    assert TimeSharingType.M1.value == "m1"
    assert TimeSharingType.M5.value == "m5"
    assert ("1分钟分时", "m1") in TimeSharingType.as_tuples()

    # 达人与基金榜单
    assert GoldRankType.TOTAL_RETURN.value == "1"
    assert ("累计收益榜", "1") in GoldRankType.as_tuples()
    assert FundRankType.GOLD_ETF.value == 401
    assert FundRankType.GOLD_ETF.desc == "黄金ETF基金排行"
    assert ("黄金ETF基金排行", 401) in FundRankType.as_tuples()
    assert FundRankSortBy.DEFAULT.value == 0
    assert ("综合排序", 0) in FundRankSortBy.as_tuples()

    # 模拟账户与计量单位
    assert SimAccountType.DEFAULT.value == 1
    assert SimTradeUnit.GRAM.value == 1
    assert SimTradeUnit.AMOUNT.value == 2
    assert SimTradeUnit.RATIO.value == 3
    assert ("按克重委托", 1) in SimTradeUnit.as_tuples()
    assert ("按金额委托", 2) in SimTradeUnit.as_tuples()
    assert ("按比例委托", 3) in SimTradeUnit.as_tuples()

    # 收益日历维度
    assert IncomeCalendarTimeType.DAY.value == "day"
    assert IncomeCalendarTradeType.ALL.value == "1"
    assert IncomeCalendarTradeType.BUY.value == "2"
    assert ("全部交易方向", "1") in IncomeCalendarTradeType.as_tuples()

    # 条件挂单状态
    assert ConditionalOrderStatus.PENDING.value == "1"
    assert ("有效生效中", "1") in ConditionalOrderStatus.as_tuples()

    # 真实订单状态、渠道与业务模式
    assert OrderStatus.SUCCESS.value == "SUCCESS"
    assert OrderTradeType.BUY.value == "BUY"
    assert OrderChannel.APP.value == "APP"
    assert OrderBusiness.GOLD_REGULAR.value == "GOLD_REGULAR"

    # 快讯频道与新闻查询
    assert NewsFlashTag.GOLD.value == 20225
    assert ("黄金专区快讯", 20225) in NewsFlashTag.as_tuples()
    assert GoldNewsQuery.GOLD.value == "黄金"
    assert ("黄金资讯", "黄金") in GoldNewsQuery.as_tuples()

    # 分析指标能力与技能
    assert AnalysisIndicatorType.BOMB.value == "bomb"
    assert AnalysisSkill.GOLD_ANALYSIS_HUB.value == "gold-analysis-hub"


def test_market_service_delegates_to_client() -> None:
    async def run() -> None:
        client = MagicMock(spec=JDGoldClient)
        client.query_price = AsyncMock(return_value={"price": 100})
        client.latest_price = AsyncMock(return_value={"latest": 100})
        client.stock_quote = AsyncMock(return_value={"quote": 100})
        client.stock_kline = AsyncMock(return_value={"kline": []})
        client.news = AsyncMock(return_value={"news": []})
        client.news_flash = AsyncMock(return_value={"flash": []})
        client.morning_report = AsyncMock(return_value={"report": []})

        service = JDGoldMarketService(client)

        # 字符串位置参数兼容性测试
        assert await service.get_price("CMBC-JCJ") == {"price": 100}
        assert await service.get_latest_price() == {"latest": 100}
        assert await service.get_stock_quote("SGE-Au99.99") == {"quote": 100}
        assert await service.get_stock_kline("SGE-Au99.99", "day") == {"kline": []}

        # 强类型枚举与新入参名称 (bank / symbol) 支持测试
        assert await service.get_price(bank=Bank.CMBC) == {"price": 100}
        client.query_price.assert_called_with("CMBC-JCJ")
        assert await service.get_stock_quote(symbol=Symbol.AU9999) == {"quote": 100}
        client.stock_quote.assert_called_with("SGE-Au99.99")
        assert await service.get_stock_kline(symbol=Symbol.AUTD, k_type=KLineType.WEEK) == {"kline": []}
        client.stock_kline.assert_called_with("SGE-Au(T+D)", k_type="week")

        # 新闻检索枚举测试
        assert await service.get_news(GoldNewsQuery.GOLD, 5) == {"news": []}
        client.news.assert_called_with(query="黄金", size=5)

        assert await service.get_news_flash(NewsFlashTag.GOLD, 10, "") == {"flash": []}
        client.news_flash.assert_called_with(tag_id=20225, page_size=10, last_id="")
        assert await service.get_morning_report(20) == {"report": []}

    asyncio.run(run())


def test_trade_service_delegates_to_client() -> None:
    async def run() -> None:
        client = MagicMock(spec=JDGoldClient)
        client.holdings = AsyncMock(return_value=[{"grams": 10}])
        client.order_list = AsyncMock(return_value=[{"order": 1}])
        client.order_sum = AsyncMock(return_value={"sum": 1000})
        client.conditional_orders = AsyncMock(return_value=[{"cond": 1}])
        client.conditional_order_detail = AsyncMock(return_value={"detail": 1})
        client.income_calendar = AsyncMock(return_value={"income": []})
        client.sim_account = AsyncMock(return_value={"balance": 10000})
        client.sim_buy = AsyncMock(return_value={"buy_ok": True})
        client.sim_sell = AsyncMock(return_value={"sell_ok": True})
        client.sim_trade_overview = AsyncMock(return_value={"overview": 1})
        client.sim_trade_records = AsyncMock(return_value={"records": []})
        client.sim_time_sharing = AsyncMock(return_value={"time_sharing": []})
        client.sim_kline = AsyncMock(return_value={"sim_kline": []})

        service = JDGoldTradeService(client)

        assert await service.get_holdings() == [{"grams": 10}]

        # 真实订单查询枚举支持
        assert await service.list_orders(
            "20261101",
            "20261118",
            business=OrderBusiness.GOLD_REGULAR,
            status_list=[OrderStatus.SUCCESS],
            trade_type_list=[OrderTradeType.BUY],
            channel_ids=[OrderChannel.APP],
        ) == [{"order": 1}]
        client.order_list.assert_called_with(
            "20261101",
            "20261118",
            pageNo=1,
            pageSize=50,
            businessCode="GOLD_REGULAR",
            statusList=["SUCCESS"],
            tradeTypeCodeList=["BUY"],
            bizChannelIds=["APP"],
        )

        assert await service.get_order_summary("20261101", "20261118") == {"sum": 1000}

        # 银行与条件单枚举入参（支持 Bank 与 HoldingBank 自动提取 short code）
        assert await service.list_conditional_orders(
            Bank.CMBC,
            100,
            200,
            status_list=[ConditionalOrderStatus.PENDING],
        ) == [{"cond": 1}]
        client.conditional_orders.assert_called_with(
            "CMBC",
            100,
            200,
            jrid="",
            statusList=["1"],
            pageIndex=1,
            pageSize=10,
        )

        assert await service.get_conditional_order_detail(HoldingBank.CMBC, "uuid") == {"detail": 1}
        client.conditional_order_detail.assert_called_with("CMBC", "uuid", jrid="")

        # 收益日历枚举
        assert await service.get_income_calendar(
            time_type=IncomeCalendarTimeType.DAY,
            time="202611",
            trade_type=IncomeCalendarTradeType.ALL,
        ) == {"income": []}
        client.income_calendar.assert_called_with("day", "202611", trade_type="1")

        # 模拟盘账户与交易
        assert await service.get_or_init_sim_account(SimAccountType.DEFAULT) == {"balance": 10000}
        client.sim_account.assert_called_with(account_type=1)

        assert await service.execute_sim_buy(
            SimTradeUnit.AMOUNT,
            HoldingBank.CZB,
            trade_amount=100,
            account_type=SimAccountType.DEFAULT,
        ) == {"buy_ok": True}
        client.sim_buy.assert_called_with(2, "CZB", trade_amount=100, trade_gram=None, account_type=1)

        # 模拟卖出按比例委托 (SimTradeUnit.RATIO)
        assert await service.execute_sim_sell(
            SimTradeUnit.RATIO,
            Bank.CZB,
            trade_ratio=0.5,
            account_type=SimAccountType.DEFAULT,
        ) == {"sell_ok": True}
        client.sim_sell.assert_called_with(3, "CZB", trade_gram=None, trade_ratio=0.5, account_type=1)

        assert await service.get_sim_trade_overview(SimAccountType.DEFAULT) == {"overview": 1}
        client.sim_trade_overview.assert_called_with(account_type=1)

        assert await service.list_sim_trade_records(page_size=8, account_type=SimAccountType.DEFAULT) == {"records": []}
        client.sim_trade_records.assert_called_with(last_id=None, page_size=8, account_type=1)

        # 模拟分时与 K 线枚举测试
        assert await service.get_sim_time_sharing(IndexSymbol.JDAU, type_=TimeSharingType.M1) == {"time_sharing": []}
        client.sim_time_sharing.assert_called_with("WG-JDAU", type_="m1")

        assert await service.get_sim_kline(
            IndexSymbol.JDAU,
            k_type=KLineType.MONTH,
            af_type=KLineAdjustType.BFQ,
        ) == {"sim_kline": []}
        client.sim_kline.assert_called_with("WG-JDAU", k_type="month", af_type="bfq")

    asyncio.run(run())


def test_analysis_service_delegates_to_client() -> None:
    async def run() -> None:
        client = MagicMock(spec=JDGoldClient)
        client.gold_realtime_quote = AsyncMock(return_value={"quote": 1})
        client.gold_bomb = AsyncMock(return_value={"bomb": 1})
        client.gold_bomb_details = AsyncMock(return_value={"details": 1})
        client.gold_opportunity = AsyncMock(return_value={"opp": 1})
        client.gold_order_book = AsyncMock(return_value={"book": 1})
        client.gold_indicator_resonance = AsyncMock(return_value={"res": 1})
        client.gold_ranking = AsyncMock(return_value={"rank": 1})
        client.fund_ranking = AsyncMock(return_value={"funds": 1})

        service = JDGoldAnalysisService(client)

        # 实时报价枚举测试 (Bank, Symbol, IndexSymbol, InternationalSymbol)
        assert await service.get_realtime_quote(Bank.CMBC) == {"quote": 1}
        client.gold_realtime_quote.assert_called_with("CMBC-JCJ")
        assert await service.get_realtime_quote(Symbol.AU9999) == {"quote": 1}
        client.gold_realtime_quote.assert_called_with("SGE-Au99.99")
        assert await service.get_realtime_quote(IndexSymbol.JDAU) == {"quote": 1}
        client.gold_realtime_quote.assert_called_with("WG-JDAU")
        assert await service.get_realtime_quote(InternationalSymbol.XAUUSD) == {"quote": 1}
        client.gold_realtime_quote.assert_called_with("WG-XAUUSD")

        assert await service.get_bomb_indicators() == {"bomb": 1}
        assert await service.get_bomb_details() == {"details": 1}
        assert await service.get_opportunities() == {"opp": 1}
        assert await service.get_order_book() == {"book": 1}
        assert await service.get_indicator_resonance() == {"res": 1}

        # 排行榜枚举测试
        assert await service.get_gold_ranking(GoldRankType.TOTAL_RETURN) == {"rank": 1}
        client.gold_ranking.assert_called_with(rank_type="1")
        assert await service.get_fund_ranking(FundRankType.GOLD_ETF, FundRankSortBy.DEFAULT) == {"funds": 1}
        client.fund_ranking.assert_called_with(rank_type=401, rankSortBy=0)

    asyncio.run(run())


def test_auth_service_methods() -> None:
    async def run() -> None:
        client = MagicMock(spec=JDGoldClient)
        client.login = AsyncMock(return_value=SecretStr("https://auth.url"))
        client.complete_login = AsyncMock(return_value=None)
        store = MagicMock(spec=JDGoldAuthRepository)
        store.load = MagicMock(return_value=JDGoldAuthState(access_token=SecretStr("tok")))

        service = JDGoldAuthService(client, store, enabled=False)

        assert service.auth_state().access_token.get_secret_value() == "tok"
        assert (await service.login()).get_secret_value() == "https://auth.url"
        await service.complete_login()
        client.complete_login.assert_awaited_once()

    asyncio.run(run())


def test_gateway_initialization_and_facade(tmp_path) -> None:
    settings = Settings(data_dir=str(tmp_path), jd_access_token=SecretStr(""), jd_proactive_auth_enabled=False)
    client = MagicMock(spec=JDGoldClient)
    gateway = JDGoldGateway(client, settings)

    assert gateway.auth is not None
    assert gateway.market is not None
    assert gateway.trade is not None
    assert gateway.analysis is not None
    assert gateway.client is not None
