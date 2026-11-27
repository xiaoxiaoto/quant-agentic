"""应用级共享业务依赖。"""

import hmac
import logging
from collections import OrderedDict
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from fcntl import LOCK_EX, LOCK_NB, flock
from math import isfinite
from pathlib import Path
from threading import Lock
from typing import ClassVar
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.agents.lightgbm import LightGBMAgent
from src.agents.news import NewsAgent
from src.agents.qlib import QlibAgent
from src.agents.timesfm import TimesFMAgent
from src.agents.timesfm_multifactor import TimesFMMultiFactorAgent
from src.clients.fx_rate import FXRateClient
from src.configs.settings import Settings
from src.contracts.domains.decision import RiskBudget
from src.contracts.domains.feature import NetReturnModelProtocol
from src.contracts.domains.product import FeeRule
from src.contracts.domains.reference_quote import ReferenceQuote
from src.contracts.schemas.jd_auth import JDAuthStatusResponse
from src.repositories.jd_collection import JDCollectionRepository
from src.repositories.market_bar import MarketBarRepository
from src.repositories.product import ProductRepository
from src.repositories.qlib_experiment import QlibExperimentRepository
from src.repositories.quote import QuoteRepository
from src.repositories.reference_quote import ReferenceQuoteRepository
from src.repositories.runtime_state import RuntimeStateRepository
from src.repositories.timesfm_experiment import TimesFMExperimentRepository
from src.services.decision.decision_engine import DecisionEngine
from src.services.execution.backtest_service import BacktestService
from src.services.execution.execution_adapter import ExecutionAdapter
from src.services.execution.live_trading import LiveTradingService
from src.services.execution.paper_trading import PaperTradingService
from src.services.execution.reconciliation import ReconciliationService
from src.services.execution.rfq_execution import RFQExecutionEngine
from src.services.execution.shadow_ledger import ShadowLedger
from src.services.jd_gold.gateway import JDGoldGateway
from src.services.jd_gold.holdings import JDHoldingsCollector
from src.services.market.bar_collector import MarketBarCollector
from src.services.market.collector_quality import CollectorQualityAnalyzer
from src.services.market.data_quality import DataQualityGuard
from src.services.market.live_quotes import LiveQuoteCollector
from src.services.market.session_calendar import SessionCalendar
from src.services.prediction.feature_engine import FeatureEngine
from src.services.prediction.predictor import ModelPredictor
from src.services.prediction.training_pipeline import TrainingPipelineService
from src.services.qlib.backtest_service import QlibBacktestService
from src.services.qlib.data_exporter import QlibDataExporter
from src.services.qlib.research_service import QlibResearchService
from src.services.qlib.runtime_bridge import QlibRuntimeBridge
from src.services.qlib.signal_service import QlibSignalService
from src.services.risk.alert_service import AlertService
from src.services.risk.news_risk import NewsRiskEngine
from src.services.risk.news_sync import NewsSyncService
from src.services.risk.risk_engine import RiskEngine
from src.services.timesfm.backtest_engine import TimesFMBacktestEngine
from src.services.timesfm.dataset_builder import TimesFMDatasetBuilder
from src.services.timesfm.pipeline_service import TimesFMPipelineService
from src.services.timesfm.trainer import TimesFMTrainer


class RuntimeDependencies:
    """统一构造并共享账本、行情仓储和配置相关服务。"""

    _MAX_QUALITY_GUARDS: ClassVar[int] = 1024
    _FROM_REQUEST_LOCK: ClassVar[Lock] = Lock()

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.training_lock = Lock()
        self.qlib_lock = Lock()
        self.timesfm_lock = Lock()
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
        self.timesfm = self._build_timesfm_agent(settings)
        self.model: NetReturnModelProtocol | None = self._build_predictor_model(settings, self.timesfm)
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
        self.jd_http = httpx.AsyncClient(
            timeout=settings.jd_timeout_seconds,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
        self.fx_client = FXRateClient(self.jd_http)
        self.jd_gold = JDGoldGateway(self.jd_http, settings)
        jd_collection = JDCollectionRepository(settings.database_path)
        self.jd_holdings = JDHoldingsCollector(
            self.jd_gold,
            jd_collection,
            enabled=settings.jd_holdings_sync_enabled,
            interval=settings.jd_holdings_sync_interval_seconds,
        )
        self.news_sync = NewsSyncService(
            jd_gold=self.jd_gold,
            news_engine=self.news,
            news_agent=self.news_agent,
            repository=jd_collection,
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
                import_jsonl_on_read=False,
            ),
            enabled=settings.live_quotes_enabled,
            interval_seconds=settings.live_quotes_interval_seconds,
            stale_seconds=settings.stale_quote_threshold_seconds,
            product_codes=settings.live_quotes_products,
            on_quote_saved=bridge_callback,
            fx_client=self.fx_client,
            fx_repository=jd_collection,
        )
        self.market_bars = MarketBarCollector(
            self.jd_gold,
            MarketBarRepository(settings.database_path),
            enabled=settings.market_bars_enabled,
            interval_seconds=settings.market_bars_interval_seconds,
            kline_products=settings.market_kline_products,
            kline_intervals=settings.market_kline_intervals,
            timesharing_intervals=settings.market_timesharing_intervals,
        )
        self.features = FeatureEngine(timesfm_agent=self.timesfm, news_engine=self.news)
        self.training_pipeline = TrainingPipelineService(
            quotes=self.quotes,
            feature_engine=self.features,
            live_quotes=self.live_quotes,
            fee_rule=fee_rule,
            basis_reference_product=settings.basis_reference_product,
        )
        qlib_exporter = QlibDataExporter(
            quotes=self.quotes,
            calendar=self.calendar,
            live_quotes=self.live_quotes,
            market_bars=self.market_bars,
            fee_rule=fee_rule,
            basis_reference_product=settings.basis_reference_product,
            default_product_code=settings.default_product_code,
            default_kline_products=settings.market_kline_products,
        )
        qlib_bridge = QlibRuntimeBridge(region=settings.qlib_region)
        qlib_experiment_dir = settings.resolved_qlib_experiment_dir
        self.qlib_research = QlibResearchService(
            exporter=qlib_exporter,
            bridge=qlib_bridge,
            provider_uri=settings.resolved_qlib_provider_uri,
            experiment_dir=qlib_experiment_dir,
            experiment_repo=QlibExperimentRepository(qlib_experiment_dir),
            fee_rule=fee_rule,
            qlib_enabled=settings.qlib_enabled,
            predictor_backend=settings.predictor_backend,
            external_provider_uri=settings.resolved_qlib_external_provider_uri,
            active_model_dir=str(settings.resolved_qlib_model_dir) if settings.resolved_qlib_model_dir else None,
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
        timesfm_experiment_dir = settings.resolved_timesfm_experiment_dir
        timesfm_builder = TimesFMDatasetBuilder(
            self.quotes,
            self.calendar,
            live_quotes=self.live_quotes,
            market_bars=self.market_bars,
            fee_rule=fee_rule,
            basis_reference_product=settings.basis_reference_product,
            default_product_code=settings.default_product_code,
        )
        timesfm_repo = TimesFMExperimentRepository(timesfm_experiment_dir)
        timesfm_trainer = TimesFMTrainer(
            timesfm_builder,
            self.timesfm,
            timesfm_repo,
            timesfm_experiment_dir,
            agent_factory=self._create_offline_timesfm_agent,
        )
        self.timesfm_pipeline = TimesFMPipelineService(
            builder=timesfm_builder,
            trainer=timesfm_trainer,
            backtest=TimesFMBacktestEngine(
                timesfm_builder,
                timesfm_trainer,
                self.timesfm,
                self.decision,
                self.risk,
                fee_rule,
                agent_factory=self._create_offline_timesfm_agent,
            ),
            experiment_repo=timesfm_repo,
            data_root=settings.resolved_data_dir,
            dataset_dir=settings.resolved_timesfm_dataset_dir,
            experiment_dir=timesfm_experiment_dir,
            timesfm_enabled=settings.timesfm_enabled,
            predictor_backend=settings.predictor_backend,
            active_model_dir=str(settings.resolved_timesfm_model_dir) if settings.resolved_timesfm_model_dir else None,
        )
        self.alerts = AlertService(fee_rule=fee_rule, state_store=self.state_store)
        self.qlib_backtest = QlibBacktestService(self.qlib_research)
        self.qlib_signal = QlibSignalService(
            self.qlib_research,
            alerts=self.alerts,
            ledger=self.ledger,
            default_model_dir=str(settings.resolved_qlib_model_dir) if settings.resolved_qlib_model_dir else None,
            default_buy_thresh=settings.qlib_au9999_buy_thresh,
            default_sell_thresh=settings.qlib_au9999_sell_thresh,
            default_trade_grams=settings.qlib_au9999_trade_unit_grams,
            default_spread_pct=settings.live_reference_spread_pct,
        )
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
            timesfm_context_length=self.timesfm.context_length,
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
            timesfm_context_length=self.timesfm.context_length,
        )
        self._quality_guards_lock = Lock()
        self.quality_guards: OrderedDict[tuple[str, str], DataQualityGuard] = OrderedDict()

    def _create_offline_timesfm_agent(self, checkpoint_path: str | None = None) -> TimesFMAgent:
        """为离线训练与回测构造与线上单例完全隔离的 TimesFMAgent 实例。"""
        return self.timesfm.clone_isolated(checkpoint_path=checkpoint_path)

    @staticmethod
    def _uses_timesfm_predictor(settings: Settings) -> bool:
        """判断当前配置实际启用的预测后端是否为 TimesFM 多因子模型。"""
        if settings.predictor_backend == "timesfm":
            return True
        if settings.predictor_backend != "auto":
            return False
        return (
            settings.resolved_qlib_model_dir is None
            and not settings.lightgbm_model_dir
            and settings.resolved_timesfm_model_dir is not None
        )

    @classmethod
    def _build_timesfm_agent(cls, settings: Settings) -> TimesFMAgent:
        """构造 TimesFM Agent；仅在实际启用 TimesFM 预测后端时联动产物的权重与校准参数。"""
        checkpoint = settings.timesfm_checkpoint
        context_length = settings.timesfm_context_length
        slope, intercept = 1.0, 0.0
        model_dir = settings.resolved_timesfm_model_dir
        if model_dir is not None and cls._uses_timesfm_predictor(settings):
            manifest_path = model_dir / TimesFMMultiFactorAgent.MANIFEST_NAME
            if manifest_path.is_file():
                backbone = TimesFMMultiFactorAgent.read_backbone_config(model_dir)
                if backbone["checkpoint_path"] is not None:
                    checkpoint = str(backbone["checkpoint_path"])
                if backbone["context_length"] is not None:
                    context_length = int(backbone["context_length"])
                slope = float(backbone["calibration_slope"])
                intercept = float(backbone["calibration_intercept"])
        return TimesFMAgent(
            is_enabled=settings.timesfm_enabled,
            checkpoint_path=checkpoint,
            device=settings.timesfm_device,
            context_length=context_length,
            calibration_slope=slope,
            calibration_intercept=intercept,
        )

    @staticmethod
    def _build_predictor_model(
        settings: Settings, timesfm_agent: TimesFMAgent | None = None
    ) -> NetReturnModelProtocol | None:
        """按 predictor_backend 与模型目录配置装配 QlibAgent、LightGBMAgent 或 TimesFMMultiFactorAgent。"""
        backend = settings.predictor_backend
        qlib_dir = settings.resolved_qlib_model_dir
        timesfm_dir = settings.resolved_timesfm_model_dir
        if backend == "heuristic":
            return None
        if backend == "timesfm":
            if timesfm_dir is None:
                raise ValueError("PREDICTOR_BACKEND=timesfm 时必须配置 TIMESFM_MODEL_DIR")
            return TimesFMMultiFactorAgent(timesfm_dir, timesfm_agent=timesfm_agent)
        if backend == "qlib":
            if qlib_dir is None:
                raise ValueError("PREDICTOR_BACKEND=qlib 时必须配置 QLIB_MODEL_DIR")
            return QlibAgent(qlib_dir)
        if backend == "lightgbm":
            if not settings.lightgbm_model_dir:
                raise ValueError("PREDICTOR_BACKEND=lightgbm 时必须配置 LIGHTGBM_MODEL_DIR")
            return LightGBMAgent(settings.resolve_project_path(settings.lightgbm_model_dir))
        if qlib_dir is not None:
            return QlibAgent(qlib_dir)
        if settings.lightgbm_model_dir:
            return LightGBMAgent(settings.resolve_project_path(settings.lightgbm_model_dir))
        if timesfm_dir is not None:
            return TimesFMMultiFactorAgent(timesfm_dir, timesfm_agent=timesfm_agent)
        return None

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

    @classmethod
    def from_request(cls, request: Request) -> "RuntimeDependencies":
        """读取当前应用独立维护的依赖容器；未通过 lifespan 初始化时加锁惰性创建。"""
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is None:
            with cls._FROM_REQUEST_LOCK:
                runtime = getattr(request.app.state, "runtime", None)
                if runtime is None:
                    runtime = cls(Settings())
                    request.app.state.runtime = runtime
        return runtime

    @classmethod
    def verify_cross_site_write(cls, request: Request, allowed_origins: tuple[str, ...] = ()) -> None:
        """拦截并拒绝未在白名单中的跨站写入请求（POST/PUT/PATCH/DELETE）。"""
        if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
            return

        allowed = {item.strip().rstrip("/").lower() for item in allowed_origins if item.strip()}
        if "*" in allowed:
            return

        def normalize_host(host_str: str, scheme: str) -> str:
            normalized = host_str.strip().lower()
            if scheme == "http" and normalized.endswith(":80"):
                return normalized[:-3]
            if scheme == "https" and normalized.endswith(":443"):
                return normalized[:-4]
            return normalized

        # 收集受信任的服务端自身访问来源（兼顾直连与多跳反向代理环境）
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        request_scheme = (forwarded_proto or request.url.scheme or "http").lower()

        forwarded_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip().lower()
        direct_host = (request.headers.get("host") or request.url.netloc or "").strip().lower()

        valid_hosts = set()
        for raw_h in (forwarded_host, direct_host):
            if raw_h:
                valid_hosts.add(raw_h)
                valid_hosts.add(normalize_host(raw_h, request_scheme))

        sec_fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
        raw_origin = request.headers.get("origin", "").strip()
        raw_referer = request.headers.get("referer", "").strip()

        # 1. 优先校验 Origin 标头
        if raw_origin:
            normalized_origin = raw_origin.rstrip("/").lower()
            if normalized_origin in allowed:
                return
            if normalized_origin == "null":
                raise HTTPException(status_code=403, detail="拒绝跨站写入请求")
            parsed_origin = urlparse(raw_origin)
            origin_scheme = (parsed_origin.scheme or "").lower()
            origin_host = normalize_host(parsed_origin.netloc or "", origin_scheme)
            if sec_fetch_site == "cross-site":
                raise HTTPException(status_code=403, detail="拒绝跨站写入请求")
            if not origin_host or origin_host not in valid_hosts or origin_scheme != request_scheme:
                raise HTTPException(status_code=403, detail="拒绝跨站写入请求")
            return

        # 2. 缺失 Origin 时回退校验 Referer 标头
        if raw_referer:
            parsed_ref = urlparse(raw_referer)
            ref_scheme = (parsed_ref.scheme or "").lower()
            ref_netloc = (parsed_ref.netloc or "").lower()
            ref_host = normalize_host(ref_netloc, ref_scheme)
            ref_origin = f"{ref_scheme}://{ref_netloc}".rstrip("/").lower() if ref_scheme and ref_netloc else ""
            if ref_origin and ref_origin in allowed:
                return
            if sec_fetch_site == "cross-site":
                raise HTTPException(status_code=403, detail="拒绝跨站写入请求")
            if not ref_host or ref_host not in valid_hosts or ref_scheme != request_scheme:
                raise HTTPException(status_code=403, detail="拒绝跨站写入请求")
            return

        # 3. 若无 Origin 亦无 Referer，但浏览器明确声明 Sec-Fetch-Site: cross-site，则坚决拦截
        if sec_fetch_site == "cross-site":
            raise HTTPException(status_code=403, detail="拒绝跨站写入请求")

    @classmethod
    def verify_api_access(cls, request: Request, runtime: "RuntimeDependencies | None" = None) -> None:
        """校验 API 访问令牌及浏览器写请求同源约束，防止未授权访问与跨站请求伪造。"""
        effective_runtime = runtime if runtime is not None else cls.from_request(request)

        # 1. 优先校验跨站写入约束（POST/PUT/PATCH/DELETE）
        cls.verify_cross_site_write(request, effective_runtime.settings.allowed_origins)

        # 2. 校验 API 访问令牌（若配置）
        expected = effective_runtime.settings.api_auth_token.get_secret_value().strip()
        if expected:
            auth_header = request.headers.get("authorization", "")
            bearer = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
            api_key = request.headers.get("x-api-key", "").strip()
            provided = bearer or api_key
            if not provided or not hmac.compare_digest(provided, expected):
                raise HTTPException(status_code=401, detail="缺少或无效的 API 访问令牌")

    @staticmethod
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """避免无穷大等原始输入使校验错误响应序列化失败。"""
        detail = jsonable_encoder(
            exc.errors(), custom_encoder={float: lambda value: value if isfinite(value) else str(value)}
        )
        return JSONResponse(status_code=422, content={"detail": detail})

    @asynccontextmanager
    async def background_collection(self) -> AsyncGenerator[None, None]:
        """API 与实盘 CLI 共用启停流程，保证同一数据目录只有一个采集实例。"""
        async with AsyncExitStack() as stack:
            if (
                self.settings.live_quotes_enabled
                or self.settings.market_bars_enabled
                or self.settings.news_sync_enabled
                or self.settings.jd_holdings_sync_enabled
                or self.settings.jd_proactive_auth_enabled
            ):
                self.settings.resolved_data_dir.mkdir(parents=True, exist_ok=True)
                lock_file = stack.enter_context((self.settings.resolved_data_dir / ".jd-collection.lock").open("ab"))
                try:
                    flock(lock_file, LOCK_EX | LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError("此 DATA_DIR 已有京东采集进程，请使用单 worker") from None
                if self.settings.live_quotes_enabled:
                    await self.live_quotes.restore_history()
            await self.jd_gold.start()
            stack.push_async_callback(self.jd_gold.stop)
            await self.live_quotes.start()
            stack.push_async_callback(self.live_quotes.stop)
            await self.market_bars.start()
            stack.push_async_callback(self.market_bars.stop)
            await self.news_sync.start()
            stack.push_async_callback(self.news_sync.stop)
            await self.jd_holdings.start()
            stack.push_async_callback(self.jd_holdings.stop)
            yield

    async def close(self) -> None:
        """安全释放所有底层异步 HTTP 客户端。"""
        try:
            await self.jd_http.aclose()
        finally:
            if self.news_agent is not None:
                await self.news_agent.close()

    def resolve_data_path(self, raw_path: str) -> Path:
        """将 HTTP 接口传入的文件或目录路径约束在 DATA_DIR 内，防止路径穿越。"""
        root = self.settings.resolved_data_dir.resolve()
        candidate = Path(raw_path)
        resolved = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("路径必须位于配置的数据目录内")
        return resolved

    def resolve_output_subdir(self, raw_path: str) -> Path:
        """将输出/导出目录约束为 DATA_DIR 下的非保留子目录，禁止写入数据根目录或内部运行目录。"""
        root = self.settings.resolved_data_dir.resolve()
        resolved = self.resolve_data_path(raw_path)
        if resolved == root:
            raise ValueError("输出目录不得指向数据根目录，请指定子目录")
        reserved = (
            (root / "jd-browser-profile").resolve(),
            (root / "references").resolve(),
            self.settings.resolved_qlib_experiment_dir.resolve(),
            self.settings.resolved_timesfm_experiment_dir.resolve(),
        )
        for protected in reserved:
            if resolved == protected or resolved.is_relative_to(protected):
                raise ValueError("输出目录不得指向系统保留运行目录")
        return resolved

    def resolve_qlib_provider_path(self, raw_path: str, *, source_type: str) -> Path:
        """校验 HTTP 接口覆盖的 Qlib 数据目录路径，防止越权访问任意目录。"""
        if source_type == "external_qlib":
            ext_root = self.settings.resolved_qlib_external_provider_uri
            if ext_root is None:
                raise ValueError("未配置允许访问的外部 Qlib 数据根目录（QLIB_EXTERNAL_PROVIDER_URI）")
            allowed_root = ext_root.resolve()
            candidate = Path(raw_path)
            resolved = (allowed_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
            if not resolved.is_relative_to(allowed_root):
                raise ValueError("外部 Qlib 数据路径必须位于 QLIB_EXTERNAL_PROVIDER_URI 目录内")
            return resolved
        return self.resolve_data_path(raw_path)

    def resolve_export_path(self, raw_path: str) -> Path:
        """校验样本导出路径：必须位于 DATA_DIR 内、以 .jsonl 结尾且不得覆盖已有文件或运行期关键文件。"""
        resolved = self.resolve_data_path(raw_path)
        if resolved.suffix.lower() != ".jsonl":
            raise ValueError("样本导出路径必须以 .jsonl 结尾")
        db_path = self.settings.database_path.resolve()
        forbidden = {
            db_path,
            Path(f"{db_path}-wal"),
            Path(f"{db_path}-shm"),
            (self.settings.resolved_data_dir.resolve() / ".jd-collection.lock"),
        }
        if resolved in forbidden:
            raise ValueError("样本导出路径不得指向数据库或锁文件")
        if resolved.exists():
            raise ValueError("样本导出目标文件已存在，请更换新文件名")
        return resolved

    async def refresh_jd_after_auth(self, action_label: str) -> JDAuthStatusResponse:
        """在京东授权或续约完成后尽力同步一次持仓快照，并返回最新授权状态。"""
        try:
            await self.jd_holdings.collect_once()
        except Exception as exc:
            logging.getLogger(__name__).warning("%s后首次同步持仓失败（%s）", action_label, type(exc).__name__)
        return await self.jd_gold.auth.status_summary()

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
