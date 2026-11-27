"""TimesFM 多因子实验记录的并发安全本地持久化仓储。"""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock

from src.contracts.domains.timesfm_multifactor import TimesFMExperimentRecord


class TimesFMExperimentRepository:
    """负责 TimesFM 实验元数据 JSONL 文件的追加写入与按 run_id 去重的倒序查询。"""

    def __init__(self, experiment_dir: str | Path) -> None:
        self._experiment_dir = Path(experiment_dir)
        self._file_path = self._experiment_dir / "experiments.jsonl"
        self._lock = Lock()
        self._cache_stat: tuple[int, int] | None = None
        self._cache_records: list[TimesFMExperimentRecord] = []

    def _load_deduped_locked(self) -> list[TimesFMExperimentRecord]:
        """在持锁状态下按文件 (mtime_ns, size) 缓存并倒序去重加载全部实验记录。"""
        if not self._file_path.exists():
            self._cache_stat = None
            self._cache_records = []
            return []
        stat = self._file_path.stat()
        sig = (stat.st_mtime_ns, stat.st_size)
        if self._cache_stat == sig:
            return self._cache_records
        lines = self._file_path.read_text(encoding="utf-8").splitlines()
        records: list[TimesFMExperimentRecord] = []
        seen: set[str] = set()
        for line_no in range(len(lines), 0, -1):
            clean = lines[line_no - 1].strip()
            if not clean:
                continue
            try:
                record = TimesFMExperimentRecord.model_validate_json(clean)
            except ValueError:
                logging.getLogger(__name__).warning("跳过损坏的 TimesFM 实验记录行：%s:%d", self._file_path, line_no)
                continue
            if record.run_id in seen:
                continue
            seen.add(record.run_id)
            records.append(record)
        self._cache_stat = sig
        self._cache_records = records
        return records

    def _load_deduped(self, *, limit: int | None = None) -> list[TimesFMExperimentRecord]:
        """倒序返回按 run_id 去重的实验记录切片。"""
        with self._lock:
            records = self._load_deduped_locked()
            return list(records[:limit]) if limit is not None else list(records)

    def append(self, record: TimesFMExperimentRecord) -> None:
        """以线程安全方式追加保存一条实验记录。"""
        with self._lock:
            self._experiment_dir.mkdir(parents=True, exist_ok=True)
            with self._file_path.open("a", encoding="utf-8") as stream:
                stream.write(record.model_dump_json() + "\n")
            self._cache_stat = None

    def list_recent(self, *, limit: int = 50) -> list[TimesFMExperimentRecord]:
        """按创建时间倒序读取最近登记的去重实验记录。"""
        if limit < 0:
            raise ValueError("limit 不能为负数")
        if limit == 0:
            return []
        return self._load_deduped(limit=limit)

    def get_by_run_id(self, run_id: str) -> TimesFMExperimentRecord | None:
        """按运行标识倒序返回最新一条匹配记录，未找到返回 None。"""
        with self._lock:
            for record in self._load_deduped_locked():
                if record.run_id == run_id:
                    return record
        return None

    def count(self) -> int:
        """返回按 run_id 去重后的有效实验总数。"""
        with self._lock:
            return len(self._load_deduped_locked())
