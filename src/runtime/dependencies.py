"""应用级共享业务依赖。"""

from collections import OrderedDict
from math import isfinite
from threading import Lock

import httpx
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.agents.lightgbm import LightGBMAgent
from src.agents.news import NewsAgent
from src.agents.timesfm import TimesFMAgent
from src.clients.fx_rate import FXRateClient
from src.configs.settings import Settings
from src.contracts.domains.decision import RiskBudget
from src.contracts.domains.product import FeeRule
from src.contracts.domains.reference_quote import ReferenceQuote
from src.repositories.product import ProductRepository
from src.repositories.quote import QuoteRepository
from src.repositories.reference_quote import ReferenceQuoteRepository
from src.repositories.runtime_state import RuntimeStateRepository
from src.services.decision.decision_engine import DecisionEngine
from src.services.execution.backtest_service import BacktestService
from src.services.execution.execution_adapter import ExecutionAdapter
from src.services.execution.live_trading import LiveTradingService
from src.services.execution.paper_trading import PaperTradingService
from src.services.execution.reconciliation import ReconciliationService
from src.services.execution.rfq_execution import RFQExecutionEngine
from src.services.execution.shadow_ledger import ShadowLedger
from src.services.jd_gold.gateway import JDGoldGateway
from src.services.market.collector_quality import CollectorQualityAnalyzer
from src.services.market.data_quality import DataQualityGuard
from src.services.market.live_quotes import LiveQuoteCollector
from src.services.market.session_calendar import SessionCalendar
from src.services.prediction.feature_engine import FeatureEngine
from src.services.prediction.predictor import ModelPredictor
from src.services.prediction.training_pipeline import TrainingPipelineService
from src.services.risk.alert_service import AlertService
from src.services.risk.news_risk import NewsRiskEngine
from src.services.risk.news_sync import NewsSyncService
from src.services.risk.risk_engine import RiskEngine


class RuntimeDependencies:
    """统一构造并共享账本、行情仓储和配置相关服务。"""

    _MAX_QUALITY_GUARDS: int = 1024

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state_store = RuntimeStateRepository(settings.database_path)
        self.products = ProductRepository()
        self.calendar = SessionCalendar()
        self.collector_quality = CollectorQualityAnalyzer(calendar=self.calendar)
        fee_rule = FeeRule(buy_fee_rate=settings.buy_fee_rate, sell_fee_rate=settings.sell_fee_rate)
        self.ledger = ShadowLedger(
            initial_cash=settings.default_initial_cash,
            fee_rule=fee_rule,
            state_store=self.state_store,
            scope="live",
        )
        self.paper_ledger = ShadowLedger(
            initial_cash=settings.default_initial_cash,
            fee_rule=fee_rule,
            state_store=self.state_store,
            scope="paper",
        )
        self.quotes = QuoteRepository(storage_dir=settings.resolved_data_dir, database_path=settings.database_path)
        self.model = LightGBMAgent(settings.lightgbm_model_dir) if settings.lightgbm_model_dir else None
        self.timesfm = TimesFMAgent(
            is_enabled=settings.timesfm_enabled,
            checkpoint_path=settings.timesfm_checkpoint,
            device=settings.timesfm_device,
            context_length=settings.timesfm_context_length,
        )
        self.news = NewsRiskEngine(state_store=self.state_store)
        self.news_agent = (
            NewsAgent(
                base_url=settings.openai_base_url,
                model=settings.openai_model,
                api_key=settings.openai_api_key.get_secret_value(),
                timeout_seconds=settings.openai_timeout_seconds,
            )
            if settings.news_enabled
            else None
        )
        self.jd_http = httpx.AsyncClient(timeout=settings.jd_timeout_seconds)
        self.fx_client = FXRateClient(self.jd_http)
        self.jd_gold = JDGoldGateway(self.jd_http, settings)
        self.news_sync = NewsSyncService(
            jd_gold=self.jd_gold,
            news_engine=self.news,
            news_agent=self.news_agent,
            enabled=settings.news_sync_enabled,
            interval_seconds=settings.news_sync_interval_seconds,
            page_size=settings.news_sync_page_size,
        )
        bridge_callback = self._bridge_reference_quote if settings.synthetic_paper_quote_enabled else None
        self.live_quotes = LiveQuoteCollector(
            self.jd_gold.client,
            ReferenceQuoteRepository(
                storage_dir=settings.resolved_data_dir / "references",
                database_path=settings.database_path,
            ),
            enabled=settings.live_quotes_enabled,
            interval_seconds=settings.live_quotes_interval_seconds,
            stale_seconds=settings.stale_quote_threshold_seconds,
            product_codes=settings.live_quotes_products,
            on_quote_saved=bridge_callback,
            fx_client=self.fx_client,
        )
        self.features = FeatureEngine(timesfm_agent=self.timesfm, news_engine=self.news)
        self.training_pipeline = TrainingPipelineService(
            quotes=self.quotes,
            feature_engine=self.features,
            live_quotes=self.live_quotes,
            fee_rule=fee_rule,
            basis_reference_product=settings.basis_reference_product,
        )
        self.predictor = ModelPredictor(fee_rule=fee_rule, model=self.model)
        self.decision = DecisionEngine(
            fee_rule=fee_rule,
            news_engine=self.news,
            cooldown_seconds=settings.signal_cooldown_seconds,
        )
        risk_budget = RiskBudget(
            daily_loss_trigger_pct=settings.daily_loss_trigger_pct,
            weekly_loss_trigger_pct=settings.weekly_loss_trigger_pct,
        )
        self.risk = RiskEngine(risk_budget=risk_budget, fee_rule=fee_rule)
        self.alerts = AlertService(fee_rule=fee_rule, state_store=self.state_store)
        self.reconciliation = ReconciliationService()
        self.rfq = RFQExecutionEngine(fee_rule=fee_rule)
        self.execution = ExecutionAdapter(ledger=self.ledger, is_live_enabled=False, state_store=self.state_store)
        self.paper_execution = ExecutionAdapter(
            ledger=self.paper_ledger,
            is_live_enabled=False,
            state_store=self.state_store,
            kill_switch_checker=self.execution.is_kill_switch_active,
        )
        self.backtest = BacktestService(
            quotes=self.quotes,
            feature_engine=self.features,
            predictor=self.predictor,
            decision_engine=self.decision,
            risk_engine=self.risk,
            fee_rule=fee_rule,
        )
        self.paper = PaperTradingService(
            quotes=self.quotes,
            ledger=self.paper_ledger,
            feature_engine=self.features,
            predictor=self.predictor,
            decision_engine=self.decision,
            risk_engine=self.risk,
            rfq_engine=self.rfq,
            execution_adapter=self.paper_execution,
            jd_gold=self.jd_gold,
            live_quotes=self.live_quotes,
            synthetic_quote_enabled=settings.synthetic_paper_quote_enabled,
            synthetic_reference_product=settings.synthetic_paper_reference_product,
            synthetic_spread_pct=settings.synthetic_paper_spread_pct,
            basis_reference_product=settings.basis_reference_product,
            timesfm_enabled=settings.timesfm_enabled,
            timesfm_context_length=settings.timesfm_context_length,
        )
        self.live = LiveTradingService(
            quotes=self.quotes,
            ledger=self.ledger,
            feature_engine=self.features,
            predictor=self.predictor,
            decision_engine=self.decision,
            risk_engine=self.risk,
            alert_service=self.alerts,
            reconciliation_service=self.reconciliation,
            execution_adapter=self.execution,
            jd_gold=self.jd_gold,
            live_quotes=self.live_quotes,
            state_store=self.state_store,
            session_calendar=self.calendar,
            live_reference_assist_enabled=settings.live_reference_assist_enabled,
            live_reference_spread_pct=settings.live_reference_spread_pct,
            basis_reference_product=settings.basis_reference_product,
            default_initial_cash=settings.default_initial_cash,
            timesfm_enabled=settings.timesfm_enabled,
            timesfm_context_length=settings.timesfm_context_length,
        )
        self._quality_guards_lock = Lock()
        self.quality_guards: OrderedDict[tuple[str, str], DataQualityGuard] = OrderedDict()

    def _bridge_reference_quote(self, quote: ReferenceQuote) -> None:
        """将配置的 CNY/g 参考行情合成为仅限模拟盘使用的双边报价（标记 is_synthetic=True）。"""
        if quote.product_code != self.settings.synthetic_paper_reference_product or quote.unit != "CNY/g":
            return
        synthetic = PaperTradingService.build_synthetic_quote(
            quote,
            product_code=self.settings.default_product_code,
            spread_pct=self.settings.synthetic_paper_spread_pct,
        )
        self.quotes.save_quote(synthetic)

    @staticmethod
    def from_request(request: Request) -> "RuntimeDependencies":
        """读取当前应用独立维护的依赖容器；未通过 lifespan 初始化时惰性创建。"""
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is None:
            runtime = RuntimeDependencies(Settings())
            request.app.state.runtime = runtime
        return runtime

    @staticmethod
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """避免无穷大等原始输入使校验错误响应序列化失败。"""
        detail = jsonable_encoder(
            exc.errors(), custom_encoder={float: lambda value: value if isfinite(value) else str(value)}
        )
        return JSONResponse(status_code=422, content={"detail": detail})

    async def close(self) -> None:
        """安全释放所有底层异步 HTTP 客户端。"""
        try:
            await self.jd_http.aclose()
        finally:
            if self.news_agent is not None:
                await self.news_agent.close()

    def quality_guard(self, source_id: str, product_code: str) -> DataQualityGuard:
        """按来源及产品隔离报价序列，限制缓存容量避免内存无限增长。"""
        key = (source_id, product_code)
        with self._quality_guards_lock:
            guard = self.quality_guards.get(key)
            if guard is not None:
                self.quality_guards.move_to_end(key)
                return guard
            guard = DataQualityGuard(
                max_spread_pct=self.settings.spread_anomaly_threshold,
                stale_threshold_seconds=self.settings.stale_quote_threshold_seconds,
            )
            self.quality_guards[key] = guard
            while len(self.quality_guards) > self._MAX_QUALITY_GUARDS:
                self.quality_guards.popitem(last=False)
            return guard
