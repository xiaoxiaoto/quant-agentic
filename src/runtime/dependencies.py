"""应用级共享业务依赖。"""

from math import isfinite

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.config.settings import Settings
from src.repositories.quote_repository import QuoteRepository
from src.services.data_quality import DataQualityGuard
from src.services.decision_engine import DecisionEngine
from src.services.predictor import ModelPredictor
from src.services.risk_engine import RiskEngine
from src.services.shadow_ledger import ShadowLedger
from src.types.domain.product import FeeRule


class RuntimeDependencies:
    """统一构造并共享账本、行情仓储和配置相关服务。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        fee_rule = FeeRule(buy_fee_rate=settings.buy_fee_rate, sell_fee_rate=settings.sell_fee_rate)
        self.ledger = ShadowLedger(fee_rule=fee_rule)
        self.quotes = QuoteRepository(storage_dir=settings.data_dir)
        self.predictor = ModelPredictor(fee_rule=fee_rule)
        self.decision = DecisionEngine(fee_rule=fee_rule)
        self.risk = RiskEngine(fee_rule=fee_rule)
        self.quality_guards: dict[tuple[str, str], DataQualityGuard] = {}

    @staticmethod
    def from_request(request: Request) -> "RuntimeDependencies":
        """读取当前应用独立维护的依赖容器。"""
        return request.app.state.runtime

    @staticmethod
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """避免无穷大等原始输入使校验错误响应序列化失败。"""
        detail = jsonable_encoder(
            exc.errors(), custom_encoder={float: lambda value: value if isfinite(value) else str(value)}
        )
        return JSONResponse(status_code=422, content={"detail": detail})

    def quality_guard(self, source_id: str, product_code: str) -> DataQualityGuard:
        """按来源及产品隔离报价序列，避免跨来源误判乱序与跳价。"""
        key = (source_id, product_code)
        if key not in self.quality_guards:
            self.quality_guards[key] = DataQualityGuard(
                max_spread_pct=self.settings.spread_anomaly_threshold,
                stale_threshold_seconds=self.settings.stale_quote_threshold_seconds,
            )
        return self.quality_guards[key]
