"""京东黄金统一业务网关，聚合授权、行情、交易与智能分析方案。"""

import time

import httpx
from pydantic import SecretStr

from src.clients.jd_gold import JDGoldClient
from src.configs.settings import Settings
from src.contracts.domains.jd_auth import JDGoldAuthState
from src.repositories.jd_gold_auth import JDGoldAuthRepository
from src.services.jd_gold.analysis import JDGoldAnalysisService
from src.services.jd_gold.auth import JDGoldAuthService
from src.services.jd_gold.market import JDGoldMarketService
from src.services.jd_gold.trade import JDGoldTradeService


class JDGoldGateway:
    """聚合京东黄金授权、行情、交易及分析方案的统一网关门面。"""

    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        """初始化底层客户端与各领域业务子服务。"""
        self._store = JDGoldAuthRepository(settings.database_path, scope=settings.jd_bff_base_url)
        self._client = JDGoldClient(
            http,
            bff_base_url=settings.jd_bff_base_url,
            public_base_url=settings.jd_public_base_url,
            gateway_base_url=settings.jd_gateway_base_url,
            latest_price_url=settings.jd_latest_price_url,
            access_token=settings.jd_access_token,
            gateway_api_key=settings.jd_gateway_api_key,
            claw=settings.jd_claw,
            auth_store=self._store,
        )
        self.auth = JDGoldAuthService(self._client, self._store, enabled=settings.jd_proactive_auth_enabled)
        self.market = JDGoldMarketService(self._client)
        self.trade = JDGoldTradeService(self._client)
        self.analysis = JDGoldAnalysisService(self._client)

    @property
    def client(self) -> JDGoldClient:
        """只读暴露底层客户端，仅供 LiveQuoteCollector 等内部消费者使用。"""
        return self._client

    def auth_state(self) -> JDGoldAuthState:
        """读取最新授权状态。"""
        return self.auth.auth_state()

    async def prepare_reauthorization(self) -> bool:
        """在最后五分钟准备待确认会话，原凭据可用至实际到期。"""
        return await self.auth.prepare_reauthorization(now=time.time())

    async def login(self) -> SecretStr:
        """主动发起授权登录，获取授权地址。"""
        return await self.auth.login()

    async def complete_login(self) -> None:
        """确认完成浏览器授权并换取正式令牌。"""
        await self.auth.complete_login()

    async def start(self) -> None:
        """启动应用级授权后台检查任务。"""
        await self.auth.start()

    async def stop(self) -> None:
        """在 HTTP 连接关闭前停止授权检查。"""
        await self.auth.stop()
