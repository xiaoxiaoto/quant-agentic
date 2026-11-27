"""手动或定时运行外部黄金 K 线增量入库。"""

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date

from src.clients.external_bars import ExternalBarClient
from src.clients.macro_bars import MacroBarClient
from src.configs.settings import Settings
from src.contracts.domains.market_bar import ExternalBarSource
from src.repositories.market_bar import MarketBarRepository
from src.services.market.external_bars import ExternalBarSync


class ExternalBarsCLI:
    """使用应用数据库回填并打印分来源、周期的导入统计。"""

    @staticmethod
    def run(argv: Sequence[str] | None = None) -> int:
        """执行一次外部行情同步。"""
        parser = argparse.ArgumentParser(description=__doc__)
        sources: tuple[ExternalBarSource, ...] = ("sge_daily", "dukascopy", "histdata", "macro_daily")
        parser.add_argument("--source", action="append", choices=sources, help="可重复指定，缺省同步全部来源")
        parser.add_argument("--start", type=date.fromisoformat, help="显式回填的首日，含此日")
        parser.add_argument("--end", type=date.fromisoformat, help="回填结束日，不含此日")
        parser.add_argument(
            "--macro-csv-dir",
            default=None,
            help="可选本地宏观 CSV 容灾目录（含 usdcny/dxy/us10y.csv）",
        )
        args = parser.parse_args(argv)
        if args.start and args.end and args.start >= args.end:
            parser.error("开始日期须早于结束日期")
        settings = Settings()
        csv_dir = (
            settings.resolve_project_path(args.macro_csv_dir)
            if args.macro_csv_dir
            else settings.resolved_qlib_macro_csv_dir
        )
        with ExternalBarClient.create_http_client() as http:
            sync = ExternalBarSync(
                MarketBarRepository(settings.database_path),
                ExternalBarClient(http),
                macro_client=MacroBarClient(http, csv_dir=csv_dir),
            )
            reports = sync.run(args.source or sources, start=args.start, end=args.end)
        print(json.dumps({name: asdict(result) for name, result in reports.items()}, ensure_ascii=False, indent=2))
        return int(any(result.error for result in reports.values()))


if __name__ == "__main__":
    raise SystemExit(ExternalBarsCLI.run())
