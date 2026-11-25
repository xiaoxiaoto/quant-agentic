"""京东黄金各接口实时调用与监控脚本。

功能：
- 实时抓取公开行情（民生积存金单价、上金所 SGE Au99.99 现货行情与日 K 线、黄金资讯）
- 检查并调用财富网关智能分析接口（需配置 JD_GATEWAY_API_KEY）
- 检查并调用账户与模拟交易接口（需京东账户登录授权）
- 支持一次性全景诊断（once）和实时高频行情监听轮询（watch）

使用方式：
    # 1. 一次性获取全量行情与接口健康诊断
    uv run python scripts/fetch_jd_data.py

    # 2. 实时高频监听民生金价与上金所现货跳价（默认每 5 秒刷新）
    uv run python scripts/fetch_jd_data.py --watch --interval 5

    # 3. 发起浏览器登录授权，获取授权 URL
    uv run python scripts/fetch_jd_data.py --login

    # 4. 浏览器授权完成后换取凭据并存入本地仓储
    uv run python scripts/fetch_jd_data.py --complete-login
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.configs.settings import Settings
from src.services.jd_gold.gateway import JDGoldGateway


class JDRealtimeFetcher:
    """京东黄金实时接口调用与行情监控器。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化应用设置与网关依赖。"""
        self._settings = settings or Settings()

    async def run_once(self, symbol: str = "SGE-Au99.99") -> dict[str, Any]:
        """单次调用京东各维度接口并输出结构化诊断报告。"""
        results: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "public_market": {},
            "analysis_gateway": {},
            "user_account": {},
        }

        async with httpx.AsyncClient(timeout=self._settings.jd_timeout_seconds) as client:
            gateway = JDGoldGateway(client, self._settings)

            print("=" * 80)
            print("【1. 公开参考行情与现货市场】（无需登录）")
            print("=" * 80)

            # 1.1 积存金最新参考单价
            try:
                latest_raw = await gateway.market.get_latest_price()
                res_data = latest_raw.get("resultData") if isinstance(latest_raw, dict) else None
                datas = res_data.get("datas") if isinstance(res_data, dict) else None
                if isinstance(datas, dict):
                    price = datas.get("price")
                    up_down = datas.get("upAndDownRate")
                    results["public_market"]["latest_price"] = datas
                    print(f"  [√] 积存金单价: {price} 元/克 (涨跌幅: {up_down})")
            except Exception as exc:
                results["public_market"]["latest_price_error"] = str(exc)
                print(f"  [×] 积存金单价获取失败: {exc}")

            # 1.2 上金所现货行情
            try:
                quote_raw = await gateway.market.get_stock_quote(symbol)
                if isinstance(quote_raw, dict):
                    name = quote_raw.get("stockName", symbol)
                    curr = quote_raw.get("currentPrice")
                    high = quote_raw.get("maxPrice")
                    low = quote_raw.get("minPrice")
                    results["public_market"]["stock_quote"] = {
                        "name": name,
                        "currentPrice": curr,
                        "maxPrice": high,
                        "minPrice": low,
                    }
                    print(f"  [√] {name} ({symbol}) 现货价: {curr} 元/克 (最高: {high}, 最低: {low})")
                else:
                    print(f"  [√] {symbol} 现货行情返回: {quote_raw}")
            except Exception as exc:
                results["public_market"]["stock_quote_error"] = str(exc)
                print(f"  [×] {symbol} 现货行情获取失败: {exc}")

            # 1.3 贵金属历史日 K 线
            try:
                kline_raw = await gateway.market.get_stock_kline(symbol, k_type="day")
                if isinstance(kline_raw, dict):
                    klines = kline_raw.get("kLineDtoList")
                    if isinstance(klines, list):
                        latest_bar = klines[-1] if klines else {}
                        results["public_market"]["kline_count"] = len(klines)
                        results["public_market"]["latest_bar"] = latest_bar
                        print(f"  [√] {symbol} 日 K 线根数: {len(klines)}, 最新柱: {latest_bar}")
                else:
                    print(f"  [√] {symbol} K 线数据返回结构: {type(kline_raw)}")
            except Exception as exc:
                results["public_market"]["kline_error"] = str(exc)
                print(f"  [×] {symbol} K 线数据获取失败: {exc}")

            # 1.4 黄金资讯检索
            try:
                news_raw = await gateway.market.get_news(query="黄金", size=2)
                if isinstance(news_raw, dict):
                    items = news_raw.get("informationList")
                    if isinstance(items, list):
                        results["public_market"]["news_count"] = len(items)
                        titles = [item.get("title", "") for item in items if isinstance(item, dict)]
                        print(f"  [√] 黄金资讯检索: 获取 {len(items)} 条，最新: {titles[:1]}")
                else:
                    print(f"  [√] 黄金资讯返回: {news_raw}")
            except Exception as exc:
                results["public_market"]["news_error"] = str(exc)
                print(f"  [×] 黄金资讯获取失败: {exc}")

            print()
            print("=" * 80)
            print("【2. 财富网关智能分析接口】（需 JD_GATEWAY_API_KEY）")
            print("=" * 80)

            # 2.1 基金排行
            try:
                fund_rank = await gateway.analysis.get_fund_ranking()
                results["analysis_gateway"]["fund_ranking"] = "success"
                print(f"  [√] 黄金基金排行: 成功返回数据 ({type(fund_rank)})")
            except Exception as exc:
                results["analysis_gateway"]["fund_ranking_error"] = str(exc)
                print(f"  [!] 黄金基金排行: 未调用成功 ({exc})")

            # 2.2 机会雷达
            try:
                opps = await gateway.analysis.get_opportunities()
                results["analysis_gateway"]["opportunities"] = "success"
                print(f"  [√] 黄金机会分析: 成功返回数据 ({type(opps)})")
            except Exception as exc:
                results["analysis_gateway"]["opportunities_error"] = str(exc)
                print(f"  [!] 黄金机会分析: 未调用成功 ({exc})")

            # 2.3 异动指标
            try:
                bombs = await gateway.analysis.get_bomb_indicators()
                results["analysis_gateway"]["bombs"] = "success"
                print(f"  [√] 黄金异动雷达: 成功返回数据 ({type(bombs)})")
            except Exception as exc:
                results["analysis_gateway"]["bombs_error"] = str(exc)
                print(f"  [!] 黄金异动雷达: 未调用成功 ({exc})")

            print()
            print("=" * 80)
            print("【3. 京东账户与持仓交易接口】（需 OAuth 授权）")
            print("=" * 80)

            auth_state = gateway.auth_state()
            token_val = auth_state.access_token.get_secret_value()
            is_authed = bool(token_val)
            results["user_account"]["is_authenticated"] = is_authed

            if is_authed:
                actual_expiry = auth_state.actual_expires_at
                exp_desc = (
                    datetime.fromtimestamp(actual_expiry).strftime("%Y-%m-%d %H:%M:%S")
                    if actual_expiry is not None
                    else "永久/未知"
                )
                print(f"  [√] 账户授权状态: 已认证 (有效至: {exp_desc})")
                try:
                    holdings = await gateway.trade.get_holdings()
                    results["user_account"]["holdings"] = holdings
                    print(f"  [√] 真实持仓查询: 成功 ({holdings})")
                except Exception as exc:
                    print(f"  [×] 真实持仓查询失败: {exc}")

                try:
                    sim_acc = await gateway.trade.get_or_init_sim_account()
                    results["user_account"]["sim_account"] = sim_acc
                    print(f"  [√] 模拟账户查询: 成功 ({sim_acc})")
                except Exception as exc:
                    print(f"  [×] 模拟账户查询失败: {exc}")
            else:
                print("  [!] 账户授权状态: 未登录/已失效。")
                print("      提示：如需测试持仓及模拟交易，请使用 --login 获取授权链接并在浏览器完成登录。")

            print("=" * 80)
            return results

    async def watch_market(self, symbol: str = "SGE-Au99.99", interval_seconds: float = 5.0) -> None:
        """实时流式轮询上金所现货与民生积存金跳价。"""
        print("=" * 80)
        print(f"启动京东黄金实时行情监听器 [标的: {symbol}, 刷新周期: {interval_seconds}s]")
        print("按 Ctrl+C 可停止监听")
        print("=" * 80)

        async with httpx.AsyncClient(timeout=self._settings.jd_timeout_seconds) as client:
            gateway = JDGoldGateway(client, self._settings)
            last_sge_price: str | None = None

            while True:
                now_str = datetime.now().strftime("%H:%M:%S")
                try:
                    # 并行请求民生金价与上金所现货
                    ms_task = asyncio.create_task(gateway.market.get_latest_price())
                    sge_task = asyncio.create_task(gateway.market.get_stock_quote(symbol))
                    ms_resp, sge_resp = await asyncio.gather(ms_task, sge_task, return_exceptions=True)

                    ms_price = "-"
                    if isinstance(ms_resp, dict):
                        res_d = ms_resp.get("resultData")
                        datas = res_d.get("datas") if isinstance(res_d, dict) else None
                        if isinstance(datas, dict):
                            ms_price = str(datas.get("price", "-"))

                    sge_price = "-"
                    sge_high = "-"
                    sge_low = "-"
                    if isinstance(sge_resp, dict):
                        sge_price = str(sge_resp.get("currentPrice", "-"))
                        sge_high = str(sge_resp.get("maxPrice", "-"))
                        sge_low = str(sge_resp.get("minPrice", "-"))

                    sge_delta = ""
                    if last_sge_price and sge_price != "-" and last_sge_price != "-":
                        diff = float(sge_price) - float(last_sge_price)
                        if diff > 0:
                            sge_delta = f" (↑ +{diff:.2f})"
                        elif diff < 0:
                            sge_delta = f" (↓ {diff:.2f})"
                        else:
                            sge_delta = " (平)"

                    print(
                        f"[{now_str}] 民生金价: {ms_price:>7} 元/克 | "
                        f"上金所现货: {sge_price:>7} 元/克{sge_delta:<10} | "
                        f"高/低: {sge_high}/{sge_low}"
                    )

                    last_sge_price = sge_price

                except Exception as exc:
                    print(f"[{now_str}] 采集异常: {exc}")

                await asyncio.sleep(interval_seconds)

    async def handle_login(self) -> None:
        """发起 PKCE 登录流程并输出浏览器授权地址。"""
        async with httpx.AsyncClient(timeout=self._settings.jd_timeout_seconds) as client:
            gateway = JDGoldGateway(client, self._settings)
            url_secret = await gateway.login()
            url = url_secret.get_secret_value()
            print("=" * 80)
            print("【京东黄金授权登录】")
            print("请复制以下授权链接至已登录京东的浏览器中打开并完成授权：")
            print()
            print(f"    {url}")
            print()
            print("授权完成后，执行以下命令完成凭据换取：")
            print("    uv run python scripts/fetch_jd_data.py --complete-login")
            print("=" * 80)

    async def handle_complete_login(self) -> None:
        """完成登录授权换取令牌并持久化。"""
        async with httpx.AsyncClient(timeout=self._settings.jd_timeout_seconds) as client:
            gateway = JDGoldGateway(client, self._settings)
            print("正在向京东服务器换取正式访问令牌...")
            try:
                await gateway.complete_login()
                state = gateway.auth_state()
                actual_expiry = state.actual_expires_at
                exp_desc = (
                    datetime.fromtimestamp(actual_expiry).strftime("%Y-%m-%d %H:%M:%S")
                    if actual_expiry is not None
                    else "永久/未知"
                )
                print("=" * 80)
                print(f"[√] 登录成功！凭据有效至: {exp_desc}")
                print("=" * 80)
            except Exception as exc:
                print(f"[×] 换取令牌失败: {exc}")


def main() -> None:
    """命令行参数解析与入口调度。"""
    parser = argparse.ArgumentParser(description="京东黄金实时接口调用与行情监控脚本")
    parser.add_argument("--watch", action="store_true", help="启用高频实时行情流式监听模式")
    parser.add_argument("--interval", type=float, default=5.0, help="监听模式下的轮询间隔（秒），默认 5.0")
    parser.add_argument("--symbol", type=str, default="SGE-Au99.99", help="贵金属标的代码，默认 SGE-Au99.99")
    parser.add_argument("--login", action="store_true", help="发起京东 OAuth 登录并输出授权链接")
    parser.add_argument("--complete-login", action="store_true", help="完成浏览器授权后换取令牌")
    parser.add_argument("--save-json", type=str, default="", help="将一次性查询结果保存至指定 JSON 文件路径")

    args = parser.parse_args()
    fetcher = JDRealtimeFetcher()

    if args.login:
        asyncio.run(fetcher.handle_login())
    elif args.complete_login:
        asyncio.run(fetcher.handle_complete_login())
    elif args.watch:
        try:
            asyncio.run(fetcher.watch_market(symbol=args.symbol, interval_seconds=args.interval))
        except KeyboardInterrupt:
            print("\n监听已停止。")
    else:
        results = asyncio.run(fetcher.run_once(symbol=args.symbol))
        if args.save_json:
            path = Path(args.save_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\n结果已成功导出至: {path.resolve()}")


if __name__ == "__main__":
    main()
