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
