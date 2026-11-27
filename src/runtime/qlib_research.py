"""Qlib 离线量化研究命令行入口（状态查询、数据导出、因子评估与模型训练）。

用法示例：
    uv run python -m src.runtime.qlib_research status
    uv run python -m src.runtime.qlib_research export --source reference --freq 1h
    uv run python -m src.runtime.qlib_research factors --source local_sqlite --preset gold_core
    uv run python -m src.runtime.qlib_research train --source local_sqlite --horizons 1d 1w \\
        --output-model data/models/qlib/v1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import get_args

from src.configs.settings import Settings
from src.contracts.domains.market_bar import KLineSource
from src.contracts.enums.trading import HoldingPeriod
from src.contracts.exceptions import QlibDatasetError, QlibUnavailableError
from src.contracts.schemas.qlib import (
    QlibBacktestRequest,
    QlibExportFrequency,
    QlibExportRequest,
    QlibFactorEvaluateRequest,
    QlibSignalRequest,
    QlibTrainRequest,
)
from src.runtime.dependencies import RuntimeDependencies


class QlibResearchCommand:
    """Qlib 离线量化研究统一 CLI 编排入口。"""

    @classmethod
    def _parse_custom_expressions(cls, items: list[str] | None) -> dict[str, str]:
        expressions: dict[str, str] = {}
        for item in items or []:
            if "=" not in item:
                raise ValueError(f"自定义因子格式须为 name=expression，当前值：{item}")
            name, expr = item.split("=", 1)
            expressions[name.strip()] = expr.strip()
        return expressions

    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        """构建 Qlib 研究 CLI 参数解析器。"""
        parser = argparse.ArgumentParser(
            prog="quant-agentic-qlib",
            description="Qlib 离线量化研究工具：数据导出、表达式因子评估、滚动训练与实验管理",
        )
        sub = parser.add_subparsers(dest="command", required=True)

        sub.add_parser("status", help="查看 pyqlib 可用性、本地/外部数据集状态与实验统计")

        p_export = sub.add_parser("export", help="将本地 SQLite 行情导出为标准 Qlib 二进制目录")
        p_export.add_argument("--source", choices=["quotes", "reference", "kline"], default="reference")
        p_export.add_argument("--instruments", nargs="+", default=None, help="指定导出品种编码列表")
        p_export.add_argument("--freq", choices=get_args(QlibExportFrequency), default="1h")
        p_export.add_argument("--kline-interval", default="m60", help="kline 模式源周期（默认 m60）")
        p_export.add_argument(
            "--kline-source",
            choices=get_args(KLineSource),
            help="kline 模式采集来源，如 sge_daily 或 xauusd_merged",
        )
        p_export.add_argument("--spread-pct", type=float, default=0.002)
        p_export.add_argument("--include-synthetic", action="store_true")
        p_export.add_argument("--include-macro", action="store_true", help="日频 kline 导出时按 PIT 拼入外盘与宏观特征")
        p_export.add_argument("--output-dir", default=None)

        preset_choices = ["gold_core", "alpha158_lite", "au9999_timing", "custom"]

        p_factors = sub.add_parser("factors", help="基于 Qlib 表达式引擎评估因子 IC / RankIC")
        p_factors.add_argument("--source", choices=["local_sqlite", "external_qlib"], default="local_sqlite")
        p_factors.add_argument("--provider-uri", default=None)
        p_factors.add_argument("--instruments", nargs="+", default=None)
        p_factors.add_argument("--freq", choices=["1h", "day"], default="1h")
        p_factors.add_argument("--horizon", default="1d")
        p_factors.add_argument("--preset", choices=preset_choices, default="gold_core")
        p_factors.add_argument("--label-mode", choices=["net", "gross"], default="net")
        p_factors.add_argument("--expr", action="append", default=None, help="自定义表达式 name=expression，可重复")
        p_factors.add_argument("--start-time", default=None)
        p_factors.add_argument("--end-time", default=None)

        p_train = sub.add_parser("train", help="运行 Qlib 训练、Walk-Forward 验证并可选导出 QlibAgent 产物")
        p_train.add_argument("--experiment-name", default="gold_qlib_research")
        p_train.add_argument("--source", choices=["local_sqlite", "external_qlib"], default="local_sqlite")
        p_train.add_argument("--provider-uri", default=None)
        p_train.add_argument("--instruments", nargs="+", default=None)
        p_train.add_argument("--freq", choices=["1h", "day"], default="1h")
        p_train.add_argument("--model", choices=["lgb", "linear"], default="lgb")
        p_train.add_argument("--preset", choices=preset_choices, default="gold_core")
        p_train.add_argument("--label-mode", choices=["net", "gross"], default="net")
        p_train.add_argument("--expr", action="append", default=None, help="自定义表达式 name=expression，可重复")
        p_train.add_argument("--horizons", nargs="+", default=["1d", "1w"])
        p_train.add_argument("--window-mode", choices=["expanding", "rolling"], default="expanding")
        p_train.add_argument("--train-window-bars", type=int, default=None)
        p_train.add_argument("--step-bars", type=int, default=None)
        p_train.add_argument("--robust-zscore", action="store_true")
        p_train.add_argument("--splits", type=int, default=3)
        p_train.add_argument("--rounds", type=int, default=80)
        p_train.add_argument("--max-depth", type=int, default=4)
        p_train.add_argument("--num-leaves", type=int, default=12)
        p_train.add_argument("--reg-alpha", type=float, default=1.0)
        p_train.add_argument("--reg-lambda", type=float, default=5.0)
        p_train.add_argument("--confidence", type=float, default=0.9)
        p_train.add_argument("--spread-pct", type=float, default=0.002)
        p_train.add_argument("--output-model", default=None)
        p_train.add_argument("--start-time", default=None)
        p_train.add_argument("--end-time", default=None)

        p_bt = sub.add_parser("backtest", help="运行单标的 Au99.99 滚动样本外阈值择时回测")
        p_bt.add_argument("--source", choices=["local_sqlite", "external_qlib"], default="local_sqlite")
        p_bt.add_argument("--provider-uri", default=None)
        p_bt.add_argument("--instrument", default="SGE-Au99.99")
        p_bt.add_argument("--freq", choices=["1h", "day"], default="day")
        p_bt.add_argument("--model", choices=["lgb", "linear"], default="lgb")
        p_bt.add_argument("--preset", choices=preset_choices, default="au9999_timing")
        p_bt.add_argument("--label-mode", choices=["net", "gross"], default="net")
        p_bt.add_argument("--expr", action="append", default=None)
        p_bt.add_argument("--horizon", default="1d")
        p_bt.add_argument("--window-mode", choices=["expanding", "rolling"], default="rolling")
        p_bt.add_argument("--train-window-bars", type=int, default=750)
        p_bt.add_argument("--step-bars", type=int, default=63)
        p_bt.add_argument("--no-robust-zscore", action="store_true")
        p_bt.add_argument("--buy-thresh", type=float, default=0.001)
        p_bt.add_argument("--sell-thresh", type=float, default=-0.0005)
        p_bt.add_argument("--use-lcb-gate", action="store_true")
        p_bt.add_argument("--trade-unit", type=float, default=10.0)
        p_bt.add_argument("--open-cost", type=float, default=0.0003)
        p_bt.add_argument("--close-cost", type=float, default=0.0003)
        p_bt.add_argument("--limit-threshold", type=float, default=0.08)
        p_bt.add_argument("--initial-cash", type=float, default=100000.0)
        p_bt.add_argument("--start-time", default=None)
        p_bt.add_argument("--end-time", default=None)

        p_sig = sub.add_parser("signal", help="生成最新盘前择时信号（可选推送人工复核队列）")
        p_sig.add_argument("--source", choices=["local_sqlite", "external_qlib"], default="local_sqlite")
        p_sig.add_argument("--provider-uri", default=None)
        p_sig.add_argument("--model-dir", default=None)
        p_sig.add_argument("--instrument", default="SGE-Au99.99")
        p_sig.add_argument("--freq", choices=["1h", "day"], default="day")
        p_sig.add_argument("--horizon", default="1d")
        p_sig.add_argument("--buy-thresh", type=float, default=None)
        p_sig.add_argument("--sell-thresh", type=float, default=None)
        p_sig.add_argument("--use-lcb-gate", action="store_true")
        p_sig.add_argument("--publish-alert", action="store_true")
        p_sig.add_argument("--trade-grams", type=float, default=None)

        p_exp = sub.add_parser("experiments", help="列出历史 Qlib 实验记录")
        p_exp.add_argument("--limit", type=int, default=20)
        return parser

    @classmethod
    def run(cls, argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
        """执行指定 Qlib 子命令并输出格式化 JSON。"""
        args = cls.build_parser().parse_args(list(argv) if argv is not None else None)
        resolved = settings or Settings(timesfm_enabled=False, news_enabled=False, live_quotes_enabled=False)
        runtime = RuntimeDependencies(resolved)
        try:
            if args.command == "status":
                payload = runtime.qlib_research.get_status().model_dump(mode="json")
            elif args.command == "export":
                req = QlibExportRequest(
                    source_mode=args.source,
                    instruments=args.instruments,
                    freq=args.freq,
                    kline_interval=args.kline_interval,
                    kline_source=args.kline_source,
                    spread_pct=args.spread_pct,
                    exclude_synthetic=not args.include_synthetic,
                    include_macro_features=args.include_macro,
                    output_dir=args.output_dir,
                )
                payload = runtime.qlib_research.export_dataset(req).model_dump(mode="json")
            elif args.command == "factors":
                req = QlibFactorEvaluateRequest(
                    source_type=args.source,
                    provider_uri=args.provider_uri,
                    instruments=args.instruments,
                    freq=args.freq,
                    horizon=HoldingPeriod(args.horizon),
                    factor_preset=args.preset,
                    label_mode=args.label_mode,
                    custom_expressions=cls._parse_custom_expressions(args.expr),
                    start_time=args.start_time,
                    end_time=args.end_time,
                )
                payload = runtime.qlib_research.evaluate_factors(req).model_dump(mode="json")
            elif args.command == "train":
                req = QlibTrainRequest(
                    experiment_name=args.experiment_name,
                    source_type=args.source,
                    provider_uri=args.provider_uri,
                    instruments=args.instruments,
                    freq=args.freq,
                    model_type=args.model,
                    factor_preset=args.preset,
                    label_mode=args.label_mode,
                    custom_expressions=cls._parse_custom_expressions(args.expr),
                    horizons=[HoldingPeriod(h) for h in args.horizons],
                    window_mode=args.window_mode,
                    train_window_bars=args.train_window_bars,
                    step_bars=args.step_bars,
                    use_robust_zscore=args.robust_zscore,
                    n_splits=args.splits,
                    num_boost_round=args.rounds,
                    lgb_max_depth=args.max_depth,
                    lgb_num_leaves=args.num_leaves,
                    lgb_reg_alpha=args.reg_alpha,
                    lgb_reg_lambda=args.reg_lambda,
                    confidence_level=args.confidence,
                    spread_pct=args.spread_pct,
                    output_model_dir=args.output_model,
                    start_time=args.start_time,
                    end_time=args.end_time,
                )
                payload = runtime.qlib_research.train_and_record(req).model_dump(mode="json")
            elif args.command == "backtest":
                req = QlibBacktestRequest(
                    source_type=args.source,
                    provider_uri=args.provider_uri,
                    instrument=args.instrument,
                    freq=args.freq,
                    model_type=args.model,
                    factor_preset=args.preset,
                    label_mode=args.label_mode,
                    custom_expressions=cls._parse_custom_expressions(args.expr),
                    horizon=HoldingPeriod(args.horizon),
                    window_mode=args.window_mode,
                    train_window_bars=args.train_window_bars,
                    step_bars=args.step_bars,
                    use_robust_zscore=not args.no_robust_zscore,
                    buy_thresh=args.buy_thresh,
                    sell_thresh=args.sell_thresh,
                    use_lcb_gate=args.use_lcb_gate,
                    trade_unit_grams=args.trade_unit,
                    open_cost=args.open_cost,
                    close_cost=args.close_cost,
                    limit_threshold=args.limit_threshold,
                    initial_cash=args.initial_cash,
                    start_time=args.start_time,
                    end_time=args.end_time,
                )
                payload = runtime.qlib_backtest.run_backtest(req).model_dump(mode="json")
            elif args.command == "signal":
                req = QlibSignalRequest(
                    source_type=args.source,
                    provider_uri=args.provider_uri,
                    model_dir=args.model_dir,
                    instrument=args.instrument,
                    freq=args.freq,
                    horizon=HoldingPeriod(args.horizon),
                    buy_thresh=args.buy_thresh,
                    sell_thresh=args.sell_thresh,
                    use_lcb_gate=args.use_lcb_gate,
                    publish_alert=args.publish_alert,
                    trade_grams=args.trade_grams,
                )
                payload = runtime.qlib_signal.generate_signal(req).model_dump(mode="json")
            else:
                records = runtime.qlib_research.list_experiments(limit=args.limit)
                payload = {"total": len(records), "experiments": [r.model_dump(mode="json") for r in records]}
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        except (QlibUnavailableError, QlibDatasetError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 2
        finally:
            asyncio.run(runtime.close())

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> None:
        """进程入口。"""
        raise SystemExit(cls.run(argv))


if __name__ == "__main__":
    QlibResearchCommand.main()
