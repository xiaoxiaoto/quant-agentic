"""TimesFM 3.0 多因子模型研究流水线命令行入口（数据准备、训练、回测、导出与一键串联）。

用法示例：
    uv run python -m src.runtime.timesfm_pipeline prepare --source kline --product WG-XAUUSD --freq 1h
    uv run python -m src.runtime.timesfm_pipeline train --dataset-dir data/timesfm_datasets/<id>
    uv run python -m src.runtime.timesfm_pipeline backtest --run-id <run_id>
    uv run python -m src.runtime.timesfm_pipeline export --run-id <run_id> --output-dir data/models/timesfm/v1
    uv run python -m src.runtime.timesfm_pipeline run-all --source kline --output-model data/models/timesfm/v1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import get_args

from src.configs.settings import Settings
from src.contracts.domains.market_bar import KLineSource
from src.contracts.domains.timesfm_multifactor import (
    TimesFMFactorPreset,
    TimesFMFreq,
    TimesFMHeadModelType,
    TimesFMSourceMode,
    TimesFMTrainMode,
)
from src.contracts.enums.trading import HoldingPeriod
from src.contracts.exceptions import TimesFMDatasetError, TimesFMTrainingError
from src.contracts.schemas.timesfm_multifactor import (
    TimesFMBacktestRequest,
    TimesFMExportRequest,
    TimesFMFullPipelineRequest,
    TimesFMPrepareRequest,
    TimesFMTrainRequest,
)
from src.runtime.dependencies import RuntimeDependencies


class TimesFMPipelineCommand:
    """TimesFM 多因子研究全链路统一 CLI 编排入口。"""

    @staticmethod
    def _parse_time(raw: str | None) -> datetime | None:
        if raw is None:
            return None
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            raise ValueError("时间参数必须包含时区，例如 2026-10-01T00:00:00+00:00")
        return parsed

    @classmethod
    def _add_prepare_args(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--source", choices=get_args(TimesFMSourceMode), default="kline")
        parser.add_argument("--product", default=None, help="标的编码，缺省随来源选择默认标的")
        parser.add_argument("--freq", choices=get_args(TimesFMFreq), default="1h")
        parser.add_argument("--kline-interval", default=None)
        parser.add_argument("--kline-source", choices=get_args(KLineSource), default=None)
        parser.add_argument("--preset", choices=get_args(TimesFMFactorPreset), default="gold_multifactor")
        parser.add_argument("--context-length", type=int, default=512)
        parser.add_argument("--stride", type=int, default=1)
        parser.add_argument("--horizons", nargs="+", default=["1d", "1w"])
        parser.add_argument("--spread-pct", type=float, default=0.002)
        parser.add_argument("--include-synthetic", action="store_true")
        parser.add_argument("--output-dir", default=None)

    @classmethod
    def _add_train_args(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--experiment-name", default="gold_timesfm_multifactor")
        parser.add_argument("--train-mode", choices=get_args(TimesFMTrainMode), default="multifactor_head")
        parser.add_argument("--head", choices=get_args(TimesFMHeadModelType), default="lgb")
        parser.add_argument("--train-horizons", nargs="+", default=None, help="缺省使用数据集全部持有期")
        parser.add_argument("--splits", type=int, default=3)
        parser.add_argument("--holdout-ratio", type=float, default=0.2)
        parser.add_argument("--rounds", type=int, default=80)
        parser.add_argument("--confidence", type=float, default=0.9)
        parser.add_argument("--batch-size", type=int, default=16)
        parser.add_argument("--max-samples", type=int, default=None)
        parser.add_argument("--no-covariates", action="store_true")
        parser.add_argument("--epochs", type=int, default=1)
        parser.add_argument("--lr", type=float, default=1e-4)
        parser.add_argument("--finetune-batch-size", type=int, default=4)
        parser.add_argument("--unfreeze-layers", type=int, default=0)

    @classmethod
    def _add_backtest_args(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--initial-cash", type=float, default=100000.0)
        parser.add_argument("--trade-grams", type=float, default=2.0)
        parser.add_argument("--dca-interval", type=int, default=5)
        parser.add_argument("--start-time", default=None)
        parser.add_argument("--end-time", default=None)

    @classmethod
    def _prepare_request(cls, args: argparse.Namespace) -> TimesFMPrepareRequest:
        return TimesFMPrepareRequest(
            source_mode=args.source,
            product_code=args.product,
            freq=args.freq,
            kline_interval=args.kline_interval,
            kline_source=args.kline_source,
            factor_preset=args.preset,
            context_length=args.context_length,
            stride=args.stride,
            horizons=[HoldingPeriod(h) for h in args.horizons],
            spread_pct=args.spread_pct,
            exclude_synthetic=not args.include_synthetic,
            output_dir=args.output_dir,
        )

    @classmethod
    def _train_request(cls, args: argparse.Namespace) -> TimesFMTrainRequest:
        return TimesFMTrainRequest(
            dataset_dir=getattr(args, "dataset_dir", None),
            experiment_name=args.experiment_name,
            train_mode=args.train_mode,
            head_model_type=args.head,
            horizons=[HoldingPeriod(h) for h in args.train_horizons] if args.train_horizons else None,
            n_splits=args.splits,
            holdout_ratio=args.holdout_ratio,
            num_boost_round=args.rounds,
            confidence_level=args.confidence,
            batch_size=args.batch_size,
            max_samples=args.max_samples,
            use_covariates=not args.no_covariates,
            finetune_epochs=args.epochs,
            finetune_lr=args.lr,
            finetune_batch_size=args.finetune_batch_size,
            unfreeze_last_layers=args.unfreeze_layers,
            output_model_dir=getattr(args, "output_model", None) if args.command == "train" else None,
        )

    @classmethod
    def _backtest_request(cls, args: argparse.Namespace) -> TimesFMBacktestRequest:
        return TimesFMBacktestRequest(
            run_id=getattr(args, "run_id", None),
            model_dir=getattr(args, "model_dir", None),
            dataset_dir=getattr(args, "dataset_dir", None) if args.command == "backtest" else None,
            initial_cash=args.initial_cash,
            trade_grams_per_order=args.trade_grams,
            dca_step_interval=args.dca_interval,
            batch_size=args.batch_size,
            start_time=cls._parse_time(args.start_time),
            end_time=cls._parse_time(args.end_time),
        )

    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        """构建 TimesFM 多因子流水线 CLI 参数解析器。"""
        parser = argparse.ArgumentParser(
            prog="quant-agentic-timesfm",
            description="TimesFM 3.0 多因子模型：数据准备、训练、严格样本外回测、模型导出与实验管理",
        )
        sub = parser.add_subparsers(dest="command", required=True)
        sub.add_parser("status", help="查看 TimesFM 运行时、最近数据集与实验统计")

        cls._add_prepare_args(sub.add_parser("prepare", help="从本地 SQLite 行情按 PIT 构造多因子数据集"))

        p_train = sub.add_parser("train", help="训练融合头、可选微调主干并执行 Walk-Forward 验证")
        p_train.add_argument("--dataset-dir", default=None)
        p_train.add_argument("--output-model", default=None, help="可选：训练后一步导出不可变模型包")
        cls._add_train_args(p_train)

        p_bt = sub.add_parser("backtest", help="严格样本外 PIT 回测（预测层与交易层）")
        p_bt.add_argument("--run-id", default=None)
        p_bt.add_argument("--model-dir", default=None)
        p_bt.add_argument("--dataset-dir", default=None)
        p_bt.add_argument("--batch-size", type=int, default=16)
        cls._add_backtest_args(p_bt)

        p_exp = sub.add_parser("export", help="导出不可变模型包并写入 SHA-256 校验")
        p_exp.add_argument("--run-id", default=None, help="缺省使用最近一次实验")
        p_exp.add_argument("--output-dir", required=True)

        p_all = sub.add_parser("run-all", help="一键串联 prepare -> train -> backtest -> export")
        cls._add_prepare_args(p_all)
        cls._add_train_args(p_all)
        cls._add_backtest_args(p_all)
        p_all.add_argument("--output-model", default=None)

        p_list = sub.add_parser("experiments", help="列出历史 TimesFM 实验记录")
        p_list.add_argument("--limit", type=int, default=20)
        return parser

    @classmethod
    def run(cls, argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
        """执行指定子命令并输出格式化 JSON。"""
        args = cls.build_parser().parse_args(list(argv) if argv is not None else None)
        resolved = settings or Settings(news_enabled=False, live_quotes_enabled=False)
        runtime = RuntimeDependencies(resolved)
        pipeline = runtime.timesfm_pipeline
        try:
            if args.command == "status":
                payload = pipeline.get_status().model_dump(mode="json")
            elif args.command == "prepare":
                payload = pipeline.prepare_dataset(cls._prepare_request(args)).model_dump(mode="json")
            elif args.command == "train":
                payload = pipeline.train_and_record(cls._train_request(args)).model_dump(mode="json")
            elif args.command == "backtest":
                payload = pipeline.run_backtest(cls._backtest_request(args)).model_dump(mode="json")
            elif args.command == "export":
                export_req = TimesFMExportRequest(run_id=args.run_id, output_model_dir=args.output_dir)
                payload = pipeline.export_model(export_req).model_dump(mode="json")
            elif args.command == "run-all":
                full = TimesFMFullPipelineRequest(
                    prepare=cls._prepare_request(args),
                    train=cls._train_request(args),
                    backtest=cls._backtest_request(args),
                    output_model_dir=args.output_model,
                )
                payload = pipeline.run_full_pipeline(full).model_dump(mode="json")
            else:
                if args.limit < 1:
                    raise ValueError("limit 必须为正整数")
                records = pipeline.list_experiments(limit=args.limit)
                payload = {
                    "total": pipeline.count_experiments(),
                    "experiments": [r.model_dump(mode="json") for r in records],
                }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        except (TimesFMDatasetError, TimesFMTrainingError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 2
        finally:
            asyncio.run(runtime.close())

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> None:
        """进程入口。"""
        raise SystemExit(cls.run(argv))


if __name__ == "__main__":
    TimesFMPipelineCommand.main()
