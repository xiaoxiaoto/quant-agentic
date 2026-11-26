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

from src.configs.settings import Settings
from src.contracts.enums.trading import HoldingPeriod
from src.contracts.exceptions import QlibDatasetError, QlibUnavailableError
from src.contracts.schemas.qlib import QlibExportRequest, QlibFactorEvaluateRequest, QlibTrainRequest
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
        p_export.add_argument("--freq", choices=["1h", "day"], default="1h")
        p_export.add_argument("--kline-interval", default="m60", help="kline 模式源周期（默认 m60）")
        p_export.add_argument("--spread-pct", type=float, default=0.002)
        p_export.add_argument("--include-synthetic", action="store_true")
        p_export.add_argument("--output-dir", default=None)

        p_factors = sub.add_parser("factors", help="基于 Qlib 表达式引擎评估因子 IC / RankIC")
        p_factors.add_argument("--source", choices=["local_sqlite", "external_qlib"], default="local_sqlite")
        p_factors.add_argument("--provider-uri", default=None)
        p_factors.add_argument("--instruments", nargs="+", default=None)
        p_factors.add_argument("--freq", choices=["1h", "day"], default="1h")
        p_factors.add_argument("--horizon", default="1d")
        p_factors.add_argument("--preset", choices=["gold_core", "alpha158_lite", "custom"], default="gold_core")
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
        p_train.add_argument("--preset", choices=["gold_core", "alpha158_lite", "custom"], default="gold_core")
        p_train.add_argument("--expr", action="append", default=None, help="自定义表达式 name=expression，可重复")
        p_train.add_argument("--horizons", nargs="+", default=["1d", "1w"])
        p_train.add_argument("--splits", type=int, default=3)
        p_train.add_argument("--rounds", type=int, default=80)
        p_train.add_argument("--confidence", type=float, default=0.9)
        p_train.add_argument("--spread-pct", type=float, default=0.002)
        p_train.add_argument("--output-model", default=None)
        p_train.add_argument("--start-time", default=None)
        p_train.add_argument("--end-time", default=None)

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
                    spread_pct=args.spread_pct,
                    exclude_synthetic=not args.include_synthetic,
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
                    custom_expressions=cls._parse_custom_expressions(args.expr),
                    horizons=[HoldingPeriod(h) for h in args.horizons],
                    n_splits=args.splits,
                    num_boost_round=args.rounds,
                    confidence_level=args.confidence,
                    spread_pct=args.spread_pct,
                    output_model_dir=args.output_model,
                    start_time=args.start_time,
                    end_time=args.end_time,
                )
                payload = runtime.qlib_research.train_and_record(req).model_dump(mode="json")
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
