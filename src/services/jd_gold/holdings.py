"""京东实盘持仓的定时采集与本地快照读取。"""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from operator import attrgetter

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, JsonValue

from src.contracts.domains.jd_auth import JDGoldAuthState
from src.contracts.domains.jd_collection import JDHoldingsSnapshot
from src.repositories.database import SQLiteDatabase
from src.repositories.jd_collection import JDCollectionRepository
from src.services.jd_gold.gateway import JDGoldGateway


class _Holding(BaseModel):
    model_config = ConfigDict(extra="allow")

    bank_code: str = Field(alias="bankCode")
    bank_name: str = Field(alias="bankName")
    total_gram: FiniteFloat = Field(alias="totalGram")
    avg_cost_price: FiniteFloat = Field(alias="avgCostPrice")
    total_income: FiniteFloat | None = Field(default=None, alias="totalIncome")


class _Holdings(BaseModel):
    total_gram_all: FiniteFloat = Field(alias="totalGramAll")
    avg_cost_price: FiniteFloat | None = Field(default=None, alias="avgCostPrice")
    holding_list: list[_Holding] = Field(alias="holdingList")


class JDHoldingsCollector:
    """只在有效授权下拉取持仓，按凭据隔离持久化版本。"""

    def __init__(
        self, gateway: JDGoldGateway, repository: JDCollectionRepository, *, enabled: bool, interval: float
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._enabled = enabled
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    @staticmethod
    def _scope(state: JDGoldAuthState) -> str | None:
        fingerprint = state.credential_fingerprint
        return f"holdings:{fingerprint}" if fingerprint else None

    @staticmethod
    def _normalize(payload: JsonValue) -> dict[str, JsonValue]:
        body = payload
        for key in ("resultData", "datas", "data"):
            if isinstance(body, dict) and key in body:
                body = body[key]
        if not isinstance(body, dict) or body.get("success") is False:
            raise ValueError("京东持仓响应结构不完整")
        validated = _Holdings.model_validate(body)
        if validated.avg_cost_price is None and (validated.total_gram_all != 0 or validated.holding_list):
            raise ValueError("京东持仓响应缺少成本均价")
        if validated.total_gram_all != 0 and not validated.holding_list:
            raise ValueError("京东持仓响应缺少持仓明细")
        holdings = sorted(validated.holding_list, key=attrgetter("bank_code", "bank_name", "total_gram"))
        items: list[JsonValue] = [item.model_dump(mode="json", by_alias=True) for item in holdings]
        return {
            **body,
            "totalGramAll": validated.total_gram_all,
            "avgCostPrice": validated.avg_cost_price or 0,
            "holdingList": items,
        }

    async def _poll(self) -> None:
        logger = logging.getLogger(__name__)
        while True:
            try:
                await self.collect_once()
            except Exception as exc:
                logger.warning("京东持仓采集失败（%s）", type(exc).__name__)
            await asyncio.sleep(self._interval)

    async def collect_once(self) -> None:
        """校验整批响应后提交；变更授权期间的响应不落库。"""
        before = await self._gateway.auth.auth_state_async()
        scope = self._scope(before)
        if scope is None or not before.is_authorized():
            return
        payload = self._normalize(await self._gateway.trade.get_holdings())
        after = await self._gateway.auth.auth_state_async()
        if before.access_token == after.access_token:
            await SQLiteDatabase.run_shielded_write(self._repository.save_snapshot, scope, payload, datetime.now(UTC))

    def latest(self) -> JDHoldingsSnapshot:
        """读取本地授权与快照，按最近确认时间判定数据时效。"""
        state = self._gateway.auth_state()
        scope = self._scope(state)
        row = self._repository.latest_snapshot(scope) if scope else None
        authorized = state.is_authorized()
        if row is None:
            return JDHoldingsSnapshot(status="pending" if authorized else "auth_required")
        payload, collected_at, checked_at = row
        if not isinstance(payload, dict):
            raise ValueError("本地持仓快照格式异常")
        stale = (datetime.now(UTC) - checked_at).total_seconds() > 2 * self._interval
        return JDHoldingsSnapshot(
            data=payload,
            collected_at=collected_at,
            last_success_at=checked_at,
            status="auth_required" if not authorized else "stale" if stale else "fresh",
        )

    async def start(self) -> None:
        """随应用启动定时持仓采集。"""
        if self._enabled and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._poll())

    async def stop(self) -> None:
        """在关闭京东客户端前停止持仓采集。"""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
