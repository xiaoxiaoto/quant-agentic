"""Qlib 可选依赖运行时桥接层。

封装 pyqlib 延迟导入、版本探测、线程安全初始化、表达式数据加载与实验记录，
确保未安装 pyqlib 时主服务可正常启动并给出清晰降级提示。
"""

from __future__ import annotations

import importlib
import importlib.metadata
from pathlib import Path
from threading import RLock
from typing import Any

from src.contracts.exceptions import QlibUnavailableError


class QlibRuntimeBridge:
    """管理可选依赖 pyqlib 的延迟加载、初始化切换与表达式数据查询。"""

    def __init__(self, *, region: str = "cn") -> None:
        self._region = region
        self._lock = RLock()
        self._initialized_key: tuple[str, str, str] | None = None

    @staticmethod
    def _import_qlib() -> Any:
        try:
            return importlib.import_module("qlib")
        except ImportError as exc:
            raise QlibUnavailableError(
                "未安装可选依赖 pyqlib，请执行 `uv sync --extra qlib` 后重试 Qlib 因子计算与模型训练"
            ) from exc

    @staticmethod
    def _import_qlib_data() -> Any:
        try:
            return importlib.import_module("qlib.data")
        except ImportError as exc:
            raise QlibUnavailableError("未安装可选依赖 pyqlib，无法加载 qlib.data 表达式引擎") from exc

    @staticmethod
    def _import_qlib_workflow() -> Any:
        try:
            return importlib.import_module("qlib.workflow")
        except ImportError:
            return None

    def is_available(self) -> bool:
        """检测当前 Python 环境是否已安装 pyqlib。"""
        try:
            importlib.import_module("qlib")
        except ImportError:
            return False
        return True

    def get_version(self) -> str | None:
        """返回已安装的 pyqlib 版本号，未安装时返回 None。"""
        for pkg in ("pyqlib", "qlib"):
            try:
                return importlib.metadata.version(pkg)
            except importlib.metadata.PackageNotFoundError:
                continue
        return None

    def ensure_initialized(
        self,
        provider_uri: Path,
        *,
        region: str | None = None,
        experiment_dir: Path | None = None,
    ) -> None:
        """线程安全初始化 Qlib 运行时，切换数据目录时自动重新初始化。"""
        qlib_mod = self._import_qlib()
        resolved_uri = str(provider_uri.resolve())
        resolved_region = region or self._region
        exp_uri = str(experiment_dir.resolve()) if experiment_dir is not None else ""
        key = (resolved_uri, resolved_region, exp_uri)
        with self._lock:
            if self._initialized_key == key:
                return
            init_kwargs: dict[str, Any] = {
                "provider_uri": resolved_uri,
                "region": resolved_region,
            }
            if exp_uri:
                init_kwargs["exp_manager"] = {
                    "class": "MLflowExpManager",
                    "module_path": "qlib.workflow.expm",
                    "kwargs": {
                        "uri": f"file:{exp_uri}",
                        "default_exp_name": "quant_agentic_qlib",
                    },
                }
            try:
                qlib_mod.init(**init_kwargs)
            except Exception as exc:
                raise QlibUnavailableError(f"Qlib 初始化失败：{exc}") from exc
            self._initialized_key = key

    def fetch_features(
        self,
        *,
        provider_uri: Path,
        instruments: list[str],
        expressions: list[str],
        freq: str,
        start_time: str | None = None,
        end_time: str | None = None,
        experiment_dir: Path | None = None,
    ) -> Any:
        """在可重入锁保护下初始化并调用 Qlib D.features，防止全局 provider_uri 并发竞态。"""
        with self._lock:
            self.ensure_initialized(provider_uri, experiment_dir=experiment_dir)
            qlib_data = self._import_qlib_data()
            qlib_freq = "60min" if freq == "1h" else freq
            return qlib_data.D.features(
                instruments=instruments,
                fields=expressions,
                start_time=start_time,
                end_time=end_time,
                freq=qlib_freq,
            )

    def log_recorder_metrics(
        self,
        *,
        experiment_name: str,
        params: dict[str, Any],
        metrics: dict[str, float],
    ) -> str | None:
        """在可重入锁保护下通过 Qlib Recorder 记录实验参数与指标，不可用时安全返回 None。"""
        with self._lock:
            workflow_mod = self._import_qlib_workflow()
            if workflow_mod is None or not hasattr(workflow_mod, "R"):
                return None
            try:
                recorder_ctx = workflow_mod.R.start(experiment_name=experiment_name)
                with recorder_ctx:
                    recorder = workflow_mod.R.get_recorder()
                    workflow_mod.R.log_params(**params)
                    if metrics:
                        workflow_mod.R.log_metrics(**metrics)
                    return str(recorder.id)
            except Exception:
                return None
