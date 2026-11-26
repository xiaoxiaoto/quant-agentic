"""统一聚合配置中心。"""

from pathlib import Path

from pydantic_settings import SettingsConfigDict

from src.configs.app import AppSettings
from src.configs.jd import JDSettings
from src.configs.model import ModelSettings
from src.configs.server import ServerSettings
from src.configs.trading import TradingSettings


class Settings(AppSettings, ServerSettings, TradingSettings, ModelSettings, JDSettings):
    """整合各领域配置组的统一应用配置对象。"""

    _PROJECT_ROOT = Path(__file__).resolve().parents[2]

    model_config = SettingsConfigDict(env_file=_PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def resolved_data_dir(self) -> Path:
        """将相对数据目录锚定到项目根目录，避免不同工作目录下数据分裂。"""
        path = Path(self.data_dir)
        return path if path.is_absolute() else self._PROJECT_ROOT / path

    @property
    def database_path(self) -> Path:
        """返回应用和授权命令共用的 SQLite 路径。"""
        return self.resolved_data_dir / "quant-agentic.sqlite3"

    @property
    def resolved_qlib_provider_uri(self) -> Path:
        """返回锚定后的本地 Qlib 数据目录，默认跟随 resolved_data_dir。"""
        if self.qlib_provider_uri == "data/qlib_data":
            return self.resolved_data_dir / "qlib_data"
        return self.resolve_project_path(self.qlib_provider_uri)

    @property
    def resolved_qlib_experiment_dir(self) -> Path:
        """返回锚定后的 Qlib 实验记录目录，默认跟随 resolved_data_dir。"""
        if self.qlib_experiment_dir == "data/qlib_experiments":
            return self.resolved_data_dir / "qlib_experiments"
        return self.resolve_project_path(self.qlib_experiment_dir)

    @property
    def resolved_qlib_external_provider_uri(self) -> Path | None:
        """返回锚定后的外部标准 Qlib 数据目录。"""
        if not self.qlib_external_provider_uri:
            return None
        return self.resolve_project_path(self.qlib_external_provider_uri)

    @property
    def resolved_qlib_model_dir(self) -> Path | None:
        """返回锚定后的 Qlib 推理模型产物目录。"""
        if not self.qlib_model_dir:
            return None
        return self.resolve_project_path(self.qlib_model_dir)

    def resolve_project_path(self, raw_path: str) -> Path:
        """将相对配置路径锚定到项目根目录，绝对路径原样返回。"""
        path = Path(raw_path)
        return path if path.is_absolute() else self._PROJECT_ROOT / path
