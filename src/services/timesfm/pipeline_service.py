"""TimesFM 多因子流水线门面：数据准备、训练、回测、不可变模型包导出与实验管理。"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from src.agents.timesfm_multifactor import TimesFMMultiFactorAgent
from src.contracts.domains.timesfm_multifactor import TimesFMDatasetManifest, TimesFMExperimentRecord
from src.contracts.exceptions import TimesFMDatasetError
from src.contracts.schemas.timesfm_multifactor import (
    TimesFMBacktestReport,
    TimesFMBacktestRequest,
    TimesFMExportReport,
    TimesFMExportRequest,
    TimesFMFullPipelineReport,
    TimesFMFullPipelineRequest,
    TimesFMPrepareReport,
    TimesFMPrepareRequest,
    TimesFMStatusResponse,
    TimesFMTrainReport,
    TimesFMTrainRequest,
)
from src.repositories.timesfm_experiment import TimesFMExperimentRepository
from src.services.timesfm.backtest_engine import TimesFMBacktestEngine
from src.services.timesfm.dataset_builder import TimesFMDatasetBuilder
from src.services.timesfm.trainer import TimesFMTrainer


class TimesFMPipelineService:
    """统一编排 TimesFM 多因子研究全链路。相对路径锚定到数据目录。"""

    _CHECKSUM_NAME: ClassVar[str] = "export_checksum.json"

    def __init__(
        self,
        *,
        builder: TimesFMDatasetBuilder,
        trainer: TimesFMTrainer,
        backtest: TimesFMBacktestEngine,
        experiment_repo: TimesFMExperimentRepository,
        data_root: Path,
        dataset_dir: Path,
        experiment_dir: Path,
        timesfm_enabled: bool = False,
        predictor_backend: str = "auto",
        active_model_dir: str | None = None,
    ) -> None:
        self._builder = builder
        self._trainer = trainer
        self._backtest = backtest
        self._repo = experiment_repo
        self._data_root = data_root
        self._dataset_dir = dataset_dir
        self._experiment_dir = experiment_dir
        self._timesfm_enabled = timesfm_enabled
        self._predictor_backend = predictor_backend
        self._active_model_dir = active_model_dir

    def _resolve(self, raw: str | None) -> Path | None:
        if raw is None:
            return None
        path = Path(raw)
        return path if path.is_absolute() else self._data_root / path

    def _latest_dataset(self) -> TimesFMDatasetManifest | None:
        if not self._dataset_dir.is_dir():
            return None
        candidates = sorted(
            (p for p in self._dataset_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in candidates:
            manifest = self._builder.inspect_dataset(path)
            if manifest is not None:
                return manifest
        return None

    def _record_for(self, run_id: str | None) -> TimesFMExperimentRecord:
        record = self._repo.get_by_run_id(run_id) if run_id else next(iter(self._repo.list_recent(limit=1)), None)
        if record is None:
            raise TimesFMDatasetError("未找到可用的 TimesFM 实验记录，请先执行训练")
        return record

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()

    def get_status(self) -> TimesFMStatusResponse:
        """返回 TimesFM 多因子研究运行时状态。"""
        return TimesFMStatusResponse(
            timesfm_enabled=self._timesfm_enabled,
            predictor_backend=self._predictor_backend,
            active_model_dir=self._active_model_dir,
            dataset_dir=str(self._dataset_dir),
            experiment_dir=str(self._experiment_dir),
            experiment_count=self._repo.count(),
            latest_dataset=self._latest_dataset(),
        )

    def prepare_dataset(self, request: TimesFMPrepareRequest) -> TimesFMPrepareReport:
        """从本地行情按 PIT 构造多因子数据集。"""
        target = self._resolve(request.output_dir)
        if target is None:
            stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            target = self._dataset_dir / f"ds_{request.freq}_{stamp}_{uuid4().hex[:6]}"
        return self._builder.prepare_dataset(request, target_dir=target)

    def export_model(self, request: TimesFMExportRequest) -> TimesFMExportReport:
        """将暂存产物原子导出为不可变模型包，写入 SHA-256 校验文件并回读自检。"""
        record = self._record_for(request.run_id)
        target = self._resolve(request.output_model_dir) or Path(request.output_model_dir)
        if target.exists():
            if not target.is_dir() or any(target.iterdir()):
                raise TimesFMDatasetError("导出目录已存在非空内容或模型产物，请使用新的版本目录")
            target.rmdir()
        staging = Path(record.staging_dir)
        if not (staging / TimesFMMultiFactorAgent.MANIFEST_NAME).is_file():
            raise TimesFMDatasetError(f"暂存产物不存在：{staging}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = target.parent / f".{target.name}.tmp_{uuid4().hex[:8]}"
        temp_dir.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copy2(
                staging / TimesFMMultiFactorAgent.MANIFEST_NAME, temp_dir / TimesFMMultiFactorAgent.MANIFEST_NAME
            )
            checkpoint = staging / "timesfm_checkpoint"
            if checkpoint.is_dir():
                shutil.copytree(checkpoint, temp_dir / "timesfm_checkpoint")
            files = {
                str(path.relative_to(temp_dir)): self._sha256(path)
                for path in sorted(temp_dir.rglob("*"))
                if path.is_file() and path.name != self._CHECKSUM_NAME
            }
            (temp_dir / self._CHECKSUM_NAME).write_text(
                json.dumps(files, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            info = TimesFMMultiFactorAgent(temp_dir).get_model_info()
            temp_dir.rename(target)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        checksum_path = target / self._CHECKSUM_NAME
        self._repo.append(record.model_copy(update={"exported_model_dir": str(target)}))
        return TimesFMExportReport(
            run_id=record.run_id,
            output_model_dir=str(target),
            checksum_path=str(checksum_path),
            files=files,
            model_info=info,
            verified=True,
        )

    def train_and_record(self, request: TimesFMTrainRequest) -> TimesFMTrainReport:
        """训练、滚动评估并登记实验；指定输出目录时一步导出模型包。"""
        dataset_dir = self._resolve(request.dataset_dir)
        if dataset_dir is None:
            latest = self._latest_dataset()
            if latest is None:
                raise TimesFMDatasetError("未指定数据集目录且没有可用数据集，请先执行数据准备")
            dataset_dir = Path(latest.dataset_dir)
        report = self._trainer.train_and_record(request, dataset_dir=dataset_dir)
        if request.output_model_dir is not None:
            exported = self.export_model(
                TimesFMExportRequest(run_id=report.experiment.run_id, output_model_dir=request.output_model_dir)
            )
            report = report.model_copy(
                update={
                    "experiment": report.experiment.model_copy(update={"exported_model_dir": exported.output_model_dir})
                }
            )
        return report

    def run_backtest(self, request: TimesFMBacktestRequest) -> TimesFMBacktestReport:
        """执行严格样本外 PIT 回测。"""
        explicit_model = self._resolve(request.model_dir)
        record: TimesFMExperimentRecord | None = None
        if explicit_model is None:
            record = self._record_for(request.run_id)
            model_dir = Path(record.exported_model_dir or record.staging_dir)
        else:
            model_dir = explicit_model
        dataset_dir = self._resolve(request.dataset_dir)
        if dataset_dir is None:
            if record is not None:
                dataset_dir = Path(record.dataset_dir)
            else:
                latest = self._latest_dataset()
                if latest is None:
                    raise TimesFMDatasetError("未指定数据集目录且没有可用数据集")
                dataset_dir = Path(latest.dataset_dir)
        run_id = record.run_id if record is not None else request.run_id
        return self._backtest.run(request, model_dir=model_dir, dataset_dir=dataset_dir, run_id=run_id)

    def run_full_pipeline(self, request: TimesFMFullPipelineRequest) -> TimesFMFullPipelineReport:
        """串联数据准备、训练、样本外回测与可选导出。"""
        prepared = self.prepare_dataset(request.prepare)
        train_request = request.train.model_copy(
            update={"dataset_dir": prepared.manifest.dataset_dir, "output_model_dir": None}
        )
        trained = self.train_and_record(train_request)
        run_id = trained.experiment.run_id
        backtest_request = request.backtest.model_copy(
            update={"run_id": run_id, "model_dir": None, "dataset_dir": prepared.manifest.dataset_dir}
        )
        backtest = self.run_backtest(backtest_request)
        exported = (
            self.export_model(TimesFMExportRequest(run_id=run_id, output_model_dir=request.output_model_dir))
            if request.output_model_dir
            else None
        )
        return TimesFMFullPipelineReport(prepare=prepared, train=trained, backtest=backtest, export=exported)

    def list_experiments(self, *, limit: int = 50) -> list[TimesFMExperimentRecord]:
        """按时间倒序返回去重后的实验记录。"""
        return self._repo.list_recent(limit=limit)

    def count_experiments(self) -> int:
        """返回按 run_id 去重后的实验总数。"""
        return self._repo.count()
