"""LightGBM PIT 样本生成与 Walk-Forward 样本外验证命令行入口。

用法示例：
    uv run python -m src.runtime.train --ref-product CMBC-JCJ --output-model data/models/lightgbm/v1
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from src.configs.settings import Settings
from src.contracts.enums.trading import HoldingPeriod
from src.runtime.dependencies import RuntimeDependencies


class ModelTrainCommand:
    """一键运行 PIT 样本构建、Walk-Forward 样本外验证与 LightGBM 训练。"""

    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        """构建模型训练与滚动验证命令行参数解析器。"""
        parser = argparse.ArgumentParser(
            prog="quant-agentic-train",
            description="从本地 SQLite 历史行情生成 PIT 样本，执行 Walk-Forward 验证并训练 LightGBM 模型",
        )
        parser.add_argument("--product", default=None, help="双边报价标的编码（默认读取配置）")
        parser.add_argument("--ref-product", default=None, help="改用参考行情编码（如 CMBC-JCJ）按估算点差生成研究样本")
        parser.add_argument("--spread-pct", type=float, default=0.002, help="参考行情估算点差比例（默认 0.002）")
        parser.add_argument(
            "--horizons",
            nargs="+",
            default=["1d", "1w"],
            help="目标持有期列表（默认 1d 1w）",
        )
        parser.add_argument("--splits", type=int, default=3, help="Walk-Forward 验证折数（默认 3）")
        parser.add_argument("--rounds", type=int, default=80, help="LightGBM 迭代轮数（默认 80）")
        parser.add_argument("--confidence", type=float, default=0.9, help="置信下界水平（默认 0.9）")
        parser.add_argument("--export-samples", default=None, help="可选导出 PIT 样本 JSONL 路径")
        parser.add_argument("--output-model", default=None, help="可选最终 LightGBM 产物输出目录")
        parser.add_argument("--include-synthetic", action="store_true", help="使用双边报价库时包含模拟合成报价")
        return parser

    @classmethod
    def run(cls, argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
        """执行训练闭环并输出中文格式化 JSON 报告。"""
        args = cls.build_parser().parse_args(list(argv) if argv is not None else None)
        resolved = settings or Settings(timesfm_enabled=False, news_enabled=False, live_quotes_enabled=False)
        runtime = RuntimeDependencies(resolved)
        try:
            horizons = [HoldingPeriod(h) for h in args.horizons]
            report = runtime.training_pipeline.run_pipeline(
                product_code=args.product or resolved.default_product_code,
                reference_product_code=args.ref_product,
                spread_pct=args.spread_pct,
                horizons=horizons,
                n_splits=args.splits,
                num_boost_round=args.rounds,
                confidence_level=args.confidence,
                export_samples_path=args.export_samples,
                output_model_dir=args.output_model,
                exclude_synthetic=not args.include_synthetic,
            )
            print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
            return 0
        finally:
            asyncio.run(runtime.close())

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> None:
        """进程入口。"""
        raise SystemExit(cls.run(argv))


if __name__ == "__main__":
    ModelTrainCommand.main()
