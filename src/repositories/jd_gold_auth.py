"""权限受限的 SQLite 京东授权存储。"""

import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import Text, column, inspect, select, table
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from src.contracts.domains.jd_auth import JDGoldAuthState
from src.contracts.exceptions import StorageError
from src.repositories.database import SQLiteDatabase


class _Base(DeclarativeBase):
    """京东授权仓储的声明式模型基类。"""


class JDGoldAuth(_Base):
    """映射现有 SQLite 授权表，保留原有数据格式。"""

    __tablename__ = "jd_gold_auth"

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[str] = mapped_column(Text)


class JDGoldAuthRepository:
    """按上游地址隔离单账户授权，不记录查询结果。"""

    def __init__(self, path: str | Path, *, scope: str) -> None:
        self._path = Path(path)
        self._scope = scope.rstrip("/")
        with SQLiteDatabase.schema_lock():
            self._engine = SQLiteDatabase.create_wal_engine(self._path, hide_parameters=True)
            _Base.metadata.create_all(self._engine)
            self._import_legacy_state()

    def _import_legacy_state(self) -> None:
        """首次读取当前上游的旧授权，保留新表中已有的状态。"""
        with SQLiteDatabase.write_session(self._engine) as session:
            connection = session.connection()
            if not inspect(connection).has_table("jd_auth"):
                return
            legacy = table("jd_auth", column("scope", Text), column("payload", Text))
            rows = select(legacy.c.scope, legacy.c.payload).where(legacy.c.scope == self._scope)
            statement = insert(JDGoldAuth).from_select(["scope", "payload"], rows)
            session.execute(statement.on_conflict_do_nothing(index_elements=["scope"]))

    def load(self) -> JDGoldAuthState:
        """从数据库恢复凭据；损坏数据直接报错，不静默丢失授权且不泄漏载荷。"""
        try:
            with Session(self._engine) as session:
                row = session.get(JDGoldAuth, self._scope)
        except SQLAlchemyError as exc:
            raise StorageError("读取京东授权状态失败") from exc
        if row is None:
            return JDGoldAuthState()
        try:
            return JDGoldAuthState.model_validate_json(row.payload)
        except ValidationError:
            raise ValueError("授权状态数据损坏") from None

    def save(self, state: JDGoldAuthState) -> None:
        """凭据写入受限数据库，其他序列化仍保持脱敏。"""
        payload = state.model_dump(mode="json", exclude={"access_token", "login_verifier", "login_url"})
        payload.update(
            access_token=state.access_token.get_secret_value(),
            login_verifier=state.login_verifier.get_secret_value(),
            login_url=state.login_url.get_secret_value(),
        )
        statement = insert(JDGoldAuth).values(scope=self._scope, payload=json.dumps(payload))
        with SQLiteDatabase.write_session(self._engine) as session:
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[JDGoldAuth.scope], set_={"payload": statement.excluded.payload}
                )
            )
