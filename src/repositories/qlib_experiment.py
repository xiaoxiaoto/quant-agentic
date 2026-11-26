"""Qlib 量化实验记录的并发安全本地持久化仓储。"""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from src.contracts.domains.qlib import QlibExperimentRecord


class QlibExperimentRepository:
    """负责 Qlib 实验元数据 JSONL 文件的并发安全追加写入与倒序查询。"""

    def __init__(self, experiment_dir: str | Path) -> None:
        self._experiment_dir = Path(experiment_dir)
        self._file_path = self._experiment_dir / "experiments.jsonl"
        self._lock = Lock()

    def append(self, record: QlibExperimentRecord) -> None:
        """以线程安全方式追加保存一条 Qlib 实验记录。"""
        with self._lock:
            self._experiment_dir.mkdir(parents=True, exist_ok=True)
            with self._file_path.open("a", encoding="utf-8") as stream:
                stream.write(record.model_dump_json() + "\n")

    def list_recent(self, *, limit: int = 50) -> list[QlibExperimentRecord]:
        """按创建时间倒序读取最近登记的 Qlib 实验记录。"""
        with self._lock:
            if not self._file_path.exists():
                return []
            lines = self._file_path.read_text(encoding="utf-8").splitlines()
        records: list[QlibExperimentRecord] = []
        for line in lines:
            clean = line.strip()
            if not clean:
                continue
            try:
                records.append(QlibExperimentRecord.model_validate_json(clean))
            except ValueError:
                continue
        records.reverse()
        return records[:limit]

    def count(self) -> int:
        """返回当前已登记的有效实验总数。"""
        return len(self.list_recent(limit=100_000))
