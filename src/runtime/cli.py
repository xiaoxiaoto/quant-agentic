"""quant-agentic 统一命令行主入口调度器。

仅保留 5 个高频核心一级命令（最多 2 层嵌套），均支持读取配置默认值零参数运行：
- serve：启动 FastAPI HTTP API 服务（无参数运行默认行为）
- backtest：执行历史 PIT 回测与被动基准对比
- paper：双引擎模拟交易（status / step / run / buy / sell）
- live：实盘辅助闭环与安全控制（status / step / run / reconcile / halt / resume）
- auth：京东账户本机浏览器 PKCE 授权管理（login / complete / status / verify）

其余细粒度查询、提醒审批、行情管理与分析网关统一由 HTTP API（/api/v1/*）提供。
"""

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from src.agents.news import NewsAgentError
from src.clients.jd_gold import JDGoldError, JDGoldLoginRequiredError
from src.configs.settings import Settings
from src.contracts.domains.account import BankStatementItem
from src.contracts.enums.trading import PaperEngine, TradeDirection
from src.runtime.dependencies import RuntimeDependencies
from src.runtime.jd_auth import JDGoldAuthCommand


class QuantCLI:
    """量化积存黄金系统极简统一命令行入口。"""

    @staticmethod
    def _parse_datetime(raw: str | None, *, end_of_day: bool = False) -> datetime | None:
        """统一解析 YYYY-MM-DD 或 ISO-8601 时间字符串，缺省时区按 UTC 处理。"""
        if raw is None:
            return None
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            suffix = "T23:59:59.999999+00:00" if end_of_day else "T00:00:00+00:00"
            return datetime.fromisoformat(f"{raw}{suffix}")
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _print_json(payload: object) -> None:
        """以中文友好的格式化 JSON 输出结果。"""
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

    @staticmethod
    def _build_output_parent() -> argparse.ArgumentParser:
        """构造共享输出格式父解析器，统一支持 --json 开关。"""
        parent = argparse.ArgumentParser(add_help=False)
        parent.add_argument("--json", dest="as_json", action="store_true", help="以 JSON 格式输出结果")
        return parent

    @classmethod
    def _run_serve(cls, args: argparse.Namespace, settings: Settings) -> int:
        """启动 FastAPI HTTP API 服务。"""
        host = args.host or settings.host
        port = args.port if args.port is not None else settings.port
        reload_enabled = settings.debug if args.reload is None else bool(args.reload)
        uvicorn.run("main:app", host=host, port=port, reload=reload_enabled)
        return 0

    @classmethod
    async def _run_backtest(cls, args: argparse.Namespace, runtime: RuntimeDependencies) -> int:
        """执行历史 PIT 回测与被动基准横向对比。"""
        settings = runtime.settings
        start_time = cls._parse_datetime(args.start, end_of_day=False)
        end_time = cls._parse_datetime(args.end, end_of_day=True)
        summary = runtime.backtest.run(
            product_code=args.product or settings.default_product_code,
            start_time=start_time,
            end_time=end_time,
            quotes_file=args.file,
            initial_cash=args.cash if args.cash is not None else settings.default_initial_cash,
            trade_grams_per_order=args.grams if args.grams is not None else settings.default_trade_grams,
            dca_step_interval=args.dca_step,
        )
        if args.as_json:
            cls._print_json(summary.model_dump(mode="json"))
            return 0

        shadow = summary.shadow_report
        print("=" * 72)
        print(f"历史回测报告 | 标的：{summary.product_code} | 有效行情：{summary.quote_count} 条")
        print(
            f"策略绩效：初始资金 {shadow.initial_cash:.2f} -> 期末权益 {shadow.final_equity:.2f} | "
            f"总收益 {shadow.total_return_pct * 100:.2f}% | 最大回撤 {shadow.max_drawdown_pct * 100:.2f}% | "
            f"交易 {shadow.total_trades} 笔 | 夏普 {shadow.sharpe_ratio}"
        )
        print("-" * 72)
        for item in summary.baseline_report.metrics:
            print(
                f"基准 {item.strategy_name:<12} | 期末 {item.final_equity:.2f} | "
                f"收益 {item.total_return_pct * 100:.2f}% | 最大回撤 {item.max_drawdown_pct * 100:.2f}%"
            )
        if summary.excess_return_vs_buy_and_hold is not None:
            print(f"相对买入持有超额收益：{summary.excess_return_vs_buy_and_hold * 100:.2f}%")
        if summary.excess_return_vs_dca is not None:
            print(f"相对定投超额收益：{summary.excess_return_vs_dca * 100:.2f}%")
        print("=" * 72)
        return 0

    @classmethod
    async def _run_paper(cls, args: argparse.Namespace, runtime: RuntimeDependencies) -> int:
        """执行双引擎模拟交易子命令。"""
        settings = runtime.settings
        product_code = getattr(args, "product", None) or settings.default_product_code
        action = args.paper_action

        if action == "status":
            status = (
                await runtime.paper.get_jd_status()
                if args.engine == PaperEngine.JD.value
                else runtime.paper.get_local_status()
            )
            cls._print_json(status)
            return 0

        if action == "step":
            trade_grams = args.grams if args.grams is not None else settings.default_trade_grams
            result = runtime.paper.step_local(product_code=product_code, trade_grams=trade_grams)
            if args.as_json:
                cls._print_json(result.model_dump(mode="json"))
            else:
                print(result.message)
                print(
                    f"账户快照：可用现金 {result.snapshot.cash_available:.2f} 元 | "
                    f"黄金持仓 {result.snapshot.gold_total_g:.4f}g | "
                    f"已实现盈亏 {result.snapshot.realized_pnl:.2f} 元"
                )
            return 0

        if action == "run":
            trade_grams = args.grams if args.grams is not None else settings.default_trade_grams
            steps = 0
            try:
                while args.max_steps is None or steps < args.max_steps:
                    result = runtime.paper.step_local(product_code=product_code, trade_grams=trade_grams)
                    steps += 1
                    print(
                        f"[paper #{steps}] {result.message} | "
                        f"现金 {result.snapshot.cash_available:.2f} | 持仓 {result.snapshot.gold_total_g:.4f}g"
                    )
                    if args.max_steps is not None and steps >= args.max_steps:
                        break
                    await asyncio.sleep(args.interval)
            except KeyboardInterrupt:
                print("收到中断信号，模拟交易循环已安全停止。")
            return 0

        if action in ("buy", "sell"):
            direction = TradeDirection.BUY if action == "buy" else TradeDirection.SELL
            if args.engine == PaperEngine.JD.value:
                response = await runtime.paper.execute_jd_order(
                    direction=direction,
                    bank=args.bank,
                    grams=args.grams,
                    amount=getattr(args, "amount", None),
                    ratio=getattr(args, "ratio", None),
                    confirm=args.confirm,
                )
                cls._print_json(response)
                return 0
            if args.grams is None:
                raise ValueError("本地影子模拟盘手动下单必须指定 --grams")
            order, fill = runtime.paper.submit_local_order(
                direction=direction,
                grams=args.grams,
                product_code=product_code,
            )
            cls._print_json({"order": order.model_dump(mode="json"), "fill": fill.model_dump(mode="json")})
            return 0

        raise ValueError(f"不支持的模拟交易动作：{action}")

    @classmethod
    async def _run_live(cls, args: argparse.Namespace, runtime: RuntimeDependencies) -> int:
        """执行实盘辅助闭环与安全控制子命令。"""
        settings = runtime.settings
        product_code = getattr(args, "product", None) or settings.default_product_code
        action = args.live_action

        if action == "status":
            cls._print_json(runtime.live.get_status())
            return 0

        if action == "step":
            trade_grams = args.grams if args.grams is not None else settings.default_trade_grams
            result = runtime.live.evaluate_once(
                product_code=product_code,
                trade_grams=trade_grams,
                reference_product_code=args.ref_product,
            )
            if args.as_json:
                cls._print_json(result.model_dump(mode="json"))
            else:
                print(result.message)
                if result.alert is not None:
                    print(
                        f"提醒编号：{result.alert.alert_id} | 方向：{result.alert.action.value} | "
                        f"建议克重：{result.alert.suggested_grams}g | 有效期至：{result.alert.expires_at.isoformat()}"
                    )
            return 0

        if action == "run":
            trade_grams = args.grams if args.grams is not None else settings.default_trade_grams
            steps = 0
            await runtime.jd_gold.start()
            await runtime.live_quotes.start()
            await runtime.news_sync.start()
            try:
                while args.max_steps is None or steps < args.max_steps:
                    result = runtime.live.evaluate_once(
                        product_code=product_code,
                        trade_grams=trade_grams,
                        reference_product_code=args.ref_product,
                    )
                    steps += 1
                    print(
                        f"[live #{steps}] 风控={result.risk_state.value} | 急停={result.kill_switch_active} | "
                        f"{result.message}"
                    )
                    if args.max_steps is not None and steps >= args.max_steps:
                        break
                    await asyncio.sleep(args.interval)
            except KeyboardInterrupt:
                print("收到中断信号，实盘辅助监控循环已安全停止。")
            finally:
                try:
                    await runtime.news_sync.stop()
                finally:
                    try:
                        await runtime.live_quotes.stop()
                    finally:
                        await runtime.jd_gold.stop()
            return 0

        if action == "reconcile":
            if args.statements_file:
                if args.cash is not None or args.gold is not None:
                    raise ValueError("流水对账（--statements-file）与快照对账（--cash/--gold）不可同时指定")
                raw_items = json.loads(Path(args.statements_file).read_text(encoding="utf-8"))
                statements = [BankStatementItem.model_validate(item) for item in raw_items]
                report, risk = runtime.live.reconcile_orders(bank_statements=statements)
            else:
                if args.cash is None or args.gold is None:
                    raise ValueError("快照对账必须同时指定 --cash 与 --gold，或传入 --statements-file 执行流水对账")
                report, risk = runtime.live.reconcile_snapshot(
                    bank_cash=args.cash,
                    bank_gold_g=args.gold,
                    bank_cost_basis=args.cost,
                )
            payload = {
                "report": report.model_dump(mode="json"),
                "recommended_risk_state": risk.value,
                "kill_switch_active": runtime.execution.is_kill_switch_active(),
            }
            if args.as_json:
                cls._print_json(payload)
            else:
                print(report.summary)
                print(f"风控状态：{risk.value} | Kill-Switch：{runtime.execution.is_kill_switch_active()}")
            return 0 if report.matched else 2

        if action == "halt":
            runtime.live.trigger_kill_switch(args.reason)
            if args.as_json:
                cls._print_json({"kill_switch_active": True, "reason": args.reason})
            else:
                print(f"已触发实盘人工急停开关（原因：{args.reason}）。")
            return 0

        if action == "resume":
            runtime.live.reset_kill_switch()
            if args.as_json:
                cls._print_json({"kill_switch_active": False, "risk_state": "NORMAL"})
            else:
                print("已解除人工急停开关，实盘风控状态恢复为 NORMAL。")
            return 0

        raise ValueError(f"不支持的实盘动作：{action}")

    @classmethod
    async def _dispatch_async(cls, args: argparse.Namespace, settings: Settings) -> int:
        """在受管依赖生命周期内分发异步命令，并统一处理常见业务异常。"""
        runtime: RuntimeDependencies | None = None
        try:
            runtime = RuntimeDependencies(settings)
            if args.command == "backtest":
                return await cls._run_backtest(args, runtime)
            if args.command == "paper":
                return await cls._run_paper(args, runtime)
            if args.command == "live":
                return await cls._run_live(args, runtime)
            raise ValueError(f"未知命令：{args.command}")
        except JDGoldLoginRequiredError:
            print("京东账户尚未完成授权或登录态已过期，请先运行：uv run python main.py auth login")
            return 1
        except JDGoldError as error:
            print(f"京东接口调用失败（{error.code}）：请检查网络、网关密钥或授权状态。")
            return 1
        except (ValueError, KeyError, FileNotFoundError, RuntimeError, NewsAgentError) as error:
            print(f"执行失败：{error}")
            return 1
        finally:
            if runtime is not None:
                await runtime.close()

    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        """构建极简统一 CLI 参数解析器（5 个核心一级命令）。"""
        output_parent = cls._build_output_parent()
        parser = argparse.ArgumentParser(
            prog="quant-agentic",
            description="积存黄金量化核心入口（无参数默认启动 HTTP API 服务；详细交互接口见 /docs）",
        )
        subparsers = parser.add_subparsers(dest="command")

        # 1. serve
        serve_parser = subparsers.add_parser("serve", help="启动 FastAPI HTTP API 服务（默认命令）")
        serve_parser.add_argument("--host", default=None, help="监听主机地址（默认读取 Settings.host）")
        serve_parser.add_argument("--port", type=int, default=None, help="监听端口（默认读取 Settings.port）")
        serve_parser.add_argument(
            "--reload",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="是否开启热重载（默认跟随 Settings.debug）",
        )

        # 2. backtest
        bt_parser = subparsers.add_parser(
            "backtest",
            parents=[output_parent],
            help="执行历史 PIT 回测与被动基准策略对比",
        )
        bt_parser.add_argument("--product", default=None, help="回测产品编码（默认读取配置）")
        bt_parser.add_argument("--start", default=None, help="起始时间（YYYY-MM-DD 或 ISO-8601）")
        bt_parser.add_argument("--end", default=None, help="结束时间（YYYY-MM-DD 或 ISO-8601）")
        bt_parser.add_argument("--file", default=None, help="可选离线行情 JSONL 文件路径")
        bt_parser.add_argument("--cash", type=float, default=None, help="回测初始资金（元，默认读取配置）")
        bt_parser.add_argument("--grams", type=float, default=None, help="单次决策基准克重（克，默认读取配置）")
        bt_parser.add_argument("--dca-step", type=int, default=5, help="定投基准采样步长")

        # 3. paper
        paper_parser = subparsers.add_parser("paper", help="双引擎模拟交易（本地影子盘 / 京东远程模拟盘）")
        paper_sub = paper_parser.add_subparsers(dest="paper_action", required=True)

        p_status = paper_sub.add_parser("status", parents=[output_parent], help="查看模拟盘账户与流水状态")
        p_status.add_argument("--engine", choices=("local", "jd"), default="local", help="模拟盘引擎")

        p_step = paper_sub.add_parser("step", parents=[output_parent], help="执行单步本地模拟交易评估与撮合")
        p_step.add_argument("--product", default=None, help="产品编码（默认读取配置）")
        p_step.add_argument("--grams", type=float, default=None, help="目标交易克重（克，默认读取配置）")

        p_run = paper_sub.add_parser("run", help="持续运行本地模拟交易循环")
        p_run.add_argument("--product", default=None, help="产品编码（默认读取配置）")
        p_run.add_argument("--grams", type=float, default=None, help="目标交易克重（克，默认读取配置）")
        p_run.add_argument("--interval", type=float, default=10.0, help="轮询间隔（秒）")
        p_run.add_argument("--max-steps", type=int, default=None, help="最大执行步数（留空持续运行）")

        p_buy = paper_sub.add_parser("buy", parents=[output_parent], help="手动提交模拟买入委托")
        p_buy.add_argument("--engine", choices=("local", "jd"), default="local", help="模拟盘引擎")
        p_buy.add_argument("--product", default=None, help="本地模拟盘产品编码（默认读取配置）")
        p_buy.add_argument("--bank", default="CZB", help="京东模拟盘银行代码（默认 CZB）")
        p_buy.add_argument("--grams", type=float, default=None, help="买入克重（克）")
        p_buy.add_argument("--amount", type=float, default=None, help="买入金额（元，仅 jd 引擎支持）")
        p_buy.add_argument("--confirm", action="store_true", help="显式确认向京东模拟盘下单")

        p_sell = paper_sub.add_parser("sell", parents=[output_parent], help="手动提交模拟卖出委托")
        p_sell.add_argument("--engine", choices=("local", "jd"), default="local", help="模拟盘引擎")
        p_sell.add_argument("--product", default=None, help="本地模拟盘产品编码（默认读取配置）")
        p_sell.add_argument("--bank", default="CZB", help="京东模拟盘银行代码（默认 CZB）")
        p_sell.add_argument("--grams", type=float, default=None, help="卖出克重（克）")
        p_sell.add_argument("--ratio", type=float, default=None, help="卖出比例（仅 jd 引擎支持）")
        p_sell.add_argument("--confirm", action="store_true", help="显式确认向京东模拟盘下单")

        # 4. live
        live_parser = subparsers.add_parser("live", help="实盘辅助闭环（信号评估、对账熔断与人工急停）")
        live_sub = live_parser.add_subparsers(dest="live_action", required=True)

        live_sub.add_parser("status", parents=[output_parent], help="查看实盘风控、急停、交易时段与授权状态")

        l_step = live_sub.add_parser("step", parents=[output_parent], help="执行单次实盘信号评估并生成合规提醒")
        l_step.add_argument("--product", default=None, help="可成交报价产品编码（默认读取配置）")
        l_step.add_argument("--ref-product", default="CMBC-JCJ", help="展示用参考行情编码")
        l_step.add_argument("--grams", type=float, default=None, help="目标建议克重（克，默认读取配置）")

        l_run = live_sub.add_parser("run", help="持续运行实盘监控循环（含参考行情采集与授权检查）")
        l_run.add_argument("--product", default=None, help="可成交报价产品编码（默认读取配置）")
        l_run.add_argument("--ref-product", default="CMBC-JCJ", help="展示用参考行情编码")
        l_run.add_argument("--grams", type=float, default=None, help="目标建议克重（克，默认读取配置）")
        l_run.add_argument("--interval", type=float, default=15.0, help="评估间隔（秒）")
        l_run.add_argument("--max-steps", type=int, default=None, help="最大评估步数（留空持续运行）")

        l_recon = live_sub.add_parser(
            "reconcile",
            parents=[output_parent],
            help="核对本地影子账本与银行权威快照或流水（差异自动熔断）",
        )
        l_recon.add_argument("--cash", type=float, default=None, help="银行权威可用现金（元，快照对账）")
        l_recon.add_argument("--gold", type=float, default=None, help="银行权威黄金持仓（克，快照对账）")
        l_recon.add_argument("--cost", type=float, default=None, help="银行权威持仓均价（元/克，可选）")
        l_recon.add_argument("--statements-file", default=None, help="银行流水 JSON 文件路径（逐笔流水对账）")

        l_halt = live_sub.add_parser("halt", parents=[output_parent], help="手动触发实盘人工急停开关")
        l_halt.add_argument("--reason", default="MANUAL_EMERGENCY_HALT", help="急停原因说明")

        live_sub.add_parser("resume", parents=[output_parent], help="解除人工急停并恢复风控状态")

        # 5. auth
        auth_parser = subparsers.add_parser("auth", help="京东黄金账户本机浏览器授权管理")
        auth_parser.add_argument("action", choices=("login", "complete", "status", "verify"), help="授权操作")

        return parser

    @classmethod
    def run(cls, argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
        """解析参数并执行对应模式，返回进程退出码。"""
        parser = cls.build_parser()
        args = parser.parse_args(list(argv) if argv is not None else None)
        resolved_settings = settings or Settings()

        if args.command is None:
            args = parser.parse_args(["serve"])

        if args.command == "serve":
            return cls._run_serve(args, resolved_settings)
        if args.command == "auth":
            return asyncio.run(JDGoldAuthCommand.run_action(args.action))

        return asyncio.run(cls._dispatch_async(args, resolved_settings))

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> None:
        """主进程入口：执行命令并以返回码退出。"""
        raise SystemExit(cls.run(argv))
