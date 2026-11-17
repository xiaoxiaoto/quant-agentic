"""行情报价记录器服务。

持续记录银行客户买入价与卖出价。
具备以下核心能力：
- 计算 SHA-256 载荷哈希，确保原始数据不可篡改与全流程可审计。
- 维护 PIT 时间戳链：quote_at -> received_at -> available_at。
- 结合 DataQualityGuard 执行数据质量门禁。
- 基于日期自动轮转的不可变追加写入日志存储。
- 实时计算买卖点差、点差率与银行基差等微结构指标。
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from src.services.data_quality import DataQualityGuard
from src.types.domain.quote import QuoteDerived, QuoteRaw
from src.types.enums.trading import DataQualityState


class QuoteRecorder:
    """7x24 银行双边报价记录器，提供不可变追加写入持久化。"""

    def __init__(
        self,
        storage_dir: Path | str = "data/quotes",
        quality_guard: DataQualityGuard | None = None,
    ) -> None:
        self._storage_dir = Path(storage_dir)
        self._guard = quality_guard or DataQualityGuard()
        self._latest_quote: QuoteRaw | None = None
        self._latest_derived: QuoteDerived | None = None
        self._total_recorded: int = 0
        self._total_anomalies: int = 0
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _compute_hash(self, payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _get_daily_file_path(self, dt: datetime) -> Path:
        date_str = dt.strftime("%Y-%m-%d")
        return self._storage_dir / f"quotes_{date_str}.jsonl"

    def _append_to_log(self, quote: QuoteRaw) -> None:
        file_path = self._get_daily_file_path(quote.received_at)
        record = quote.model_dump(mode="json")
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _derive_metrics(
        self,
        quote: QuoteRaw,
        intl_ref_cny: float | None = None,
    ) -> QuoteDerived:
        spread = quote.ask_price - quote.bid_price
        mid = (quote.bid_price + quote.ask_price) / 2.0
        spread_pct = spread / mid if mid > 0 else 0.0

        basis: float | None = None
        if intl_ref_cny is not None and intl_ref_cny > 0:
            basis = (mid / intl_ref_cny) - 1.0

        return QuoteDerived(
            quote_id=quote.quote_id,
            spread=spread,
            spread_pct=spread_pct,
            mid_price=mid,
            intl_ref_cny_per_g=intl_ref_cny,
            bank_basis=basis,
        )

    def record(
        self,
        quote_id: str,
        source_id: str,
        bid_price: float,
        ask_price: float,
        quote_at: datetime,
        is_tradable: bool,
        raw_payload: str = "",
        response_code: str | None = "200",
        intl_ref_cny: float | None = None,
        now: datetime | None = None,
    ) -> tuple[QuoteRaw, QuoteDerived]:
        """记录并校验一条新到达的银行报价。"""
        received_at = now or self._now()
        payload_hash = self._compute_hash(raw_payload or f"{quote_id}:{bid_price}:{ask_price}")

        quote = QuoteRaw(
            quote_id=quote_id,
            source_id=source_id,
            bid_price=bid_price,
            ask_price=ask_price,
            quote_at=quote_at,
            received_at=received_at,
            available_at=None,
            is_tradable=is_tradable,
            response_code=response_code,
            payload_hash=payload_hash,
            quality_state=DataQualityState.NORMAL,
        )

        quality_state = self._guard.validate_and_classify(quote, now=received_at)
        quote.quality_state = quality_state

        if quality_state == DataQualityState.NORMAL:
            quote.available_at = received_at
            self._latest_quote = quote
            self._latest_derived = self._derive_metrics(quote, intl_ref_cny)
        else:
            self._total_anomalies += 1

        self._append_to_log(quote)
        self._total_recorded += 1

        derived = self._derive_metrics(quote, intl_ref_cny)
        return quote, derived

    @property
    def latest_quote(self) -> QuoteRaw | None:
        """获取最新一条正常且有效的报价。"""
        return self._latest_quote

    @property
    def latest_derived(self) -> QuoteDerived | None:
        """获取最新有效报价的微结构衍生指标。"""
        return self._latest_derived

    @property
    def total_recorded(self) -> int:
        """累计记录的报价总行数。"""
        return self._total_recorded

    @property
    def total_anomalies(self) -> int:
        """累计拦截的异常/不可用报价数。"""
        return self._total_anomalies
