"""直接调用 JDGoldGateway 统一业务网关的实时演示脚本。

直接使用网关门面中的子服务：
- gateway.market (行情与资讯服务：支持多银行积存金、上金所多标的现货与K线)
- gateway.analysis (财富网关与智能分析服务)
- gateway.trade (持仓、订单与模拟交易服务)
- gateway.auth (授权会话管理)

运行方式：
    uv run python scripts/call_jd_gateway.py
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.configs.settings import Settings
from src.contracts.enums.jd_gold import Bank, KLineType, Symbol
from src.services.jd_gold.gateway import JDGoldGateway


async def main() -> None:
    settings = Settings()

    print("=" * 80)
    print("京东黄金统一业务网关 (JDGoldGateway) 实时调用")
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    async with httpx.AsyncClient(timeout=settings.jd_timeout_seconds) as http:
        gateway = JDGoldGateway(http, settings)

        # ----------------------------------------------------------------------
        # 1. 各商业银行积存金报价对比 (gateway.market.get_price)
        # ----------------------------------------------------------------------
        print("\n[1] 商业银行积存金实时行情 (gateway.market.get_price)")
        for bank in Bank:
            bank_name = bank.desc
            try:
                res = await gateway.market.get_price(bank)
                if isinstance(res, dict):
                    price = res.get("lastPrice")
                    high = res.get("highPrice")
                    low = res.get("lowPrice")
                    rate = res.get("raisePercent")
                    rate_pct = f"{float(rate):.2%}" if isinstance(rate, (int, float)) else str(rate or "-")
                    print(
                        f"  • {bank_name:<6} ({bank.value}): 最新 {price} 元/克 "
                        f"(涨跌幅: {rate_pct:>7}, 最高: {high}, 最低: {low})"
                    )
            except Exception as exc:
                print(f"  • {bank_name} ({bank.value}) 查询异常: {exc}")

        # ----------------------------------------------------------------------
        # 2. 上金所官方场内现货与延期标的 (gateway.market.get_stock_quote)
        # ----------------------------------------------------------------------
        print("\n[2] 上海黄金交易所官方场内行情 (gateway.market.get_stock_quote)")
        for symbol in Symbol:
            name = symbol.desc
            try:
                quote = await gateway.market.get_stock_quote(symbol)
                if isinstance(quote, dict):
                    curr = quote.get("currentPrice")
                    high = quote.get("maxPrice")
                    low = quote.get("minPrice")
                    close_prev = quote.get("closedYesterday")
                    unit = "元/千克" if "Ag" in symbol.value else "元/克"
                    print(
                        f"  • {name:<7} ({symbol.value}): 现价 {curr} {unit} | "
                        f"最高: {high} | 最低: {low} | 昨收: {close_prev}"
                    )
            except Exception as exc:
                print(f"  • {name} ({symbol.value}) 查询异常: {exc}")

        # ----------------------------------------------------------------------
        # 3. 上金所日 K 线历史数据 (gateway.market.get_stock_kline)
        # ----------------------------------------------------------------------
        print("\n[3] 标的 K 线历史数据 (gateway.market.get_stock_kline)")
        try:
            kline = await gateway.market.get_stock_kline(Symbol.AU9999, k_type=KLineType.DAY)
            if isinstance(kline, dict):
                bars = kline.get("kLineDtoList")
                if isinstance(bars, list) and bars:
                    last_bar = bars[-1]
                    if isinstance(last_bar, dict):
                        open_p = last_bar.get("open")
                        close_p = last_bar.get("close")
                        high_p = last_bar.get("high")
                        low_p = last_bar.get("low")
                        date_str = last_bar.get("date")
                        print(
                            f"  • 黄金9999 日 K 线: 共 {len(bars)} 根, "
                            f"最新 [{date_str}]: 开 {open_p} 收 {close_p} 高 {high_p} 低 {low_p}"
                        )
        except Exception as exc:
            print(f"  • 黄金9999 日 K 线查询异常: {exc}")

        # ----------------------------------------------------------------------
        # 4. 实时黄金资讯检索 (gateway.market.get_news)
        # ----------------------------------------------------------------------
        print("\n[4] 实时黄金资讯检索 (gateway.market.get_news)")
        try:
            news = await gateway.market.get_news(query="黄金", size=2)
            if isinstance(news, dict):
                items = news.get("informationList")
                if isinstance(items, list) and items:
                    first = items[0]
                    if isinstance(first, dict):
                        print(f"  • 最新资讯标题: 《{first.get('title')}》")
        except Exception as exc:
            print(f"  • 黄金资讯检索异常: {exc}")

        # ----------------------------------------------------------------------
        # 5. 分析服务 (gateway.analysis，需配置 JD_GATEWAY_API_KEY)
        # ----------------------------------------------------------------------
        print("\n[5] 分析服务 (gateway.analysis)")
        try:
            fund_ranking = await gateway.analysis.get_fund_ranking()
            print(f"  • 黄金基金排行: 成功获取数据 ({type(fund_ranking)})")
        except Exception as exc:
            print(f"  • 黄金基金排行: 未调用成功 ({exc})")

        # ----------------------------------------------------------------------
        # 6. 交易与账户服务 (gateway.trade，需登录授权)
        # ----------------------------------------------------------------------
        print("\n[6] 交易与持仓服务 (gateway.trade)")
        auth_state = gateway.auth_state()
        if auth_state.access_token.get_secret_value():
            try:
                holdings = await gateway.trade.get_holdings()
                print(f"  • 真实持仓数据: {holdings}")
            except Exception as exc:
                print(f"  • 真实持仓查询异常: {exc}")
        else:
            print("  • 账户状态: 未授权（调用持仓与模拟盘需登录，可通过 gateway.login() 获取授权地址）")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
