"""时间点（Point-in-Time, PIT）行情仓储模块。

提供行情数据的不可变检索、时间点切片重放与完整性校验。
严格遵循 available_at <= decision_at 的 PIT 原则，避免前视偏差。
"""

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from src.types.domain.quote import QuoteRaw
from src.types.enums.trading import DataQualityState


class QuoteRepository:
    """时间点（Point-in-Time）行情数据仓储。

    管理本地存储的行情原始文件，提供按历史时间点回放与完整性校验。
    """

    def __init__(self, storage_dir: Path | str = "data/quotes") -> None:
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def _get_daily_files(self) -> list[Path]:
        """按时间顺序获取所有行情日志文件。"""
        if not self._storage_dir.exists():
            return []
        files = sorted(self._storage_dir.glob("quotes_*.jsonl"))
        return files

    def _parse_line(self, line: str) -> QuoteRaw | None:
        """解析单行 JSONL 行情记录。"""
        clean_line = line.strip()
        if not clean_line:
            return None
        try:
            data = json.loads(clean_line)
            return QuoteRaw.model_validate(data)
        except Exception:
            return None

    def _is_eligible_pit(self, quote: QuoteRaw, as_of: datetime) -> bool:
        """检查行情记录是否满足 PIT 可用性条件。"""
        cutoff = quote.available_at if quote.available_at is not None else quote.received_at
        return cutoff <= as_of

    def save_quote(self, quote: QuoteRaw) -> None:
        """将单条行情写入对应的日期分卷文件。"""
        date_str = quote.received_at.strftime("%Y-%m-%d")
        file_path = self._storage_dir / f"quotes_{date_str}.jsonl"
        record = quote.model_dump(mode="json")
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_latest_as_of(
        self,
        as_of: datetime,
        product_code: str = "GOLD_ACCUMULATION",
    ) -> QuoteRaw | None:
        """获取在指定历史时间点已可见的最优最新行情。

        仅返回 available_at <= as_of 的有效记录，杜绝未来信息穿越。
        """
        candidate: QuoteRaw | None = None
        for file_path in reversed(self._get_daily_files()):
            try:
                with open(file_path, encoding="utf-8") as f:
                    lines = f.readlines()
                for line in reversed(lines):
                    quote = self._parse_line(line)
                    if quote is None:
                        continue
                    if quote.product_code != product_code:
                        continue
                    if self._is_eligible_pit(quote, as_of):
                        return quote
            except OSError:
                continue
        return candidate

    def query_pit_series(
        self,
        start_time: datetime,
        end_time: datetime,
        as_of: datetime | None = None,
        product_code: str = "GOLD_ACCUMULATION",
        valid_only: bool = True,
    ) -> list[QuoteRaw]:
        """检索时间范围内满足 PIT 约束的行情序列。"""
        results: list[QuoteRaw] = []
        effective_as_of = as_of or end_time

        for file_path in self._get_daily_files():
            try:
                with open(file_path, encoding="utf-8") as f:
                    for line in f:
                        quote = self._parse_line(line)
                        if quote is None:
                            continue
                        if quote.product_code != product_code:
                            continue
                        if valid_only and quote.quality_state != DataQualityState.NORMAL:
                            continue
                        if quote.quote_at < start_time or quote.quote_at > end_time:
                            continue
                        if not self._is_eligible_pit(quote, effective_as_of):
                            continue
                        results.append(quote)
            except OSError:
                continue

        results.sort(key=lambda q: q.quote_at)
        return results

    def iter_replay(
        self,
        start_time: datetime,
        end_time: datetime,
        product_code: str = "GOLD_ACCUMULATION",
    ) -> Iterator[QuoteRaw]:
        """按时间顺序回放行情流，用于无前视偏差的仿真回测。"""
        series = self.query_pit_series(
            start_time=start_time,
            end_time=end_time,
            as_of=end_time,
            product_code=product_code,
        )
        yield from series

    def verify_file_integrity(self, file_path: Path) -> tuple[int, int]:
        """核验行情日志文件的哈希完整性。

        返回 (有效校验数, 异常破坏数)。
        """
        valid_count = 0
        corrupt_count = 0
        if not file_path.exists():
            return 0, 0

        with open(file_path, encoding="utf-8") as f:
            for line in f:
                quote = self._parse_line(line)
                if quote is None:
                    corrupt_count += 1
                    continue
                if quote.payload_hash:
                    # 重新计算哈希验证
                    expected = hashlib.sha256(
                        f"{quote.quote_id}:{quote.bid_price}:{quote.ask_price}".encode()
                    ).hexdigest()
                    if quote.payload_hash == expected:
                        valid_count += 1
                    else:
                        corrupt_count += 1
                else:
                    valid_count += 1

        return valid_count, corrupt_count
