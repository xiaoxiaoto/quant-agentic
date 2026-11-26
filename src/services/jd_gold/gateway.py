"""京东黄金统一业务网关，聚合授权、行情、交易与智能分析方案。"""

import time
from pathlib import Path

import httpx
from pydantic import SecretStr

from src.clients.jd_browser_auth import JDGoldBrowserAuthorizer
from src.clients.jd_gold import JDGoldClient
from src.configs.settings import Settings
from src.contracts.domains.jd_auth import JDGoldAuthState, JDGoldBrowserAuthorizerProtocol
from src.repositories.jd_gold_auth import JDGoldAuthRepository
from src.services.jd_gold.analysis import JDGoldAnalysisService
from src.services.jd_gold.auth import JDGoldAuthService
from src.services.jd_gold.market import JDGoldMarketService
from src.services.jd_gold.trade import JDGoldTradeService


class JDGoldGateway:
    """聚合京东黄金授权、行情、交易及分析方案的统一网关门面。"""

    def __init__(
        self,
        http: httpx.AsyncClient,
        settings: Settings,
        *,
        browser_authorizer: JDGoldBrowserAuthorizerProtocol | None = None,
    ) -> None:
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
        if browser_authorizer is None:
            profile_dir = (
                Path(settings.jd_auto_renew_profile_dir)
                if settings.jd_auto_renew_profile_dir.strip()
                else settings.resolved_data_dir / "jd-browser-profile"
            )
            browser_authorizer = JDGoldBrowserAuthorizer(
                cdp_url=settings.jd_auto_renew_cdp_url,
                headless=settings.jd_auto_renew_headless,
                profile_dir=profile_dir,
                timeout_seconds=settings.jd_auto_renew_timeout_seconds,
                confirm_selectors=settings.jd_auto_renew_confirm_selectors,
                username=settings.jd_login_username,
                password=settings.jd_login_password,
            )
        self._browser_authorizer = browser_authorizer
        self.auth = JDGoldAuthService(
            self._client,
            self._store,
            enabled=settings.jd_proactive_auth_enabled,
            browser_authorizer=self._browser_authorizer,
            auto_renew_enabled=settings.jd_auto_renew_enabled,
            reauth_lead_seconds=settings.jd_auto_renew_lead_seconds,
            auto_renew_cooldown_seconds=settings.jd_auto_renew_cooldown_seconds,
            auto_renew_max_attempts=settings.jd_auto_renew_max_attempts,
        )
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

    async def auto_renew(self, *, headless: bool | None = None) -> None:
        """通过浏览器自动化执行一次京东账号续约。"""
        await self.auth.auto_renew(headless=headless)

    async def interactive_browser_login(self, *, timeout_seconds: float = 300.0) -> None:
        """打开有头持久化浏览器窗口供人工完成首次京东登录与授权。"""
        await self.auth.interactive_browser_login(timeout_seconds=timeout_seconds)

    async def verify_auth(self) -> None:
        """实测当前京东授权凭据是否可正常访问账户接口。"""
        await self.auth.verify()

    async def logout(self) -> None:
        """清除本地京东授权凭据与待确认会话。"""
        await self.auth.logout()

    async def start(self) -> None:
        """启动应用级授权后台检查任务。"""
        await self.auth.start()

    async def stop(self) -> None:
        """在 HTTP 连接关闭前停止授权检查。"""
        await self.auth.stop()
