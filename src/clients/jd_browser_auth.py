"""京东黄金浏览器自动化续约客户端（基于 Playwright CDP / 持久化上下文）。"""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from src.clients.jd_gold import JDGoldError
from src.contracts.domains.jd_auth import JDGoldBrowserRenewError


class JDGoldBrowserAuthorizer:
    """优先通过 CDP 复用本机 Chrome 登录态完成京东 PKCE 授权续约，遇风控校验立即中止。"""

    _ALLOWED_HOSTS: frozenset[str] = frozenset({"jd.com", "jdjygold.com"})
    _LOGIN_HOST_MARKERS: tuple[str, ...] = ("passport.jd.com", "plogin.m.jd.com", "login.m.jd.com")
    _QUICK_LOGIN_SELECTORS: tuple[str, ...] = (
        "button#loginSubmit.user-is-login",
        ".user-is-login",
    )
    _AGREE_CHECKBOX_SELECTORS: tuple[str, ...] = (
        "input#agree",
        "input[name='agree'][type='checkbox']",
    )
    _AGREE_PROTOCOL_SELECTORS: tuple[str, ...] = (
        "button#agreeBtn",
        ".agreeButton",
    )
    _ACCOUNT_TAB_SELECTORS: tuple[str, ...] = (
        "a:has-text('账户登录')",
        "a:has-text('账号登录')",
        "button:has-text('密码登录')",
        "button:has-text('账号密码登录')",
        ".login-tab-r a",
        ".login-tab-r",
    )
    _USERNAME_SELECTORS: tuple[str, ...] = (
        "#loginname",
        "input[name='loginname']",
        "input[name='username']",
        "input[placeholder*='邮箱']",
        "input[placeholder*='账号']",
        "input[placeholder*='手机号']",
    )
    _PASSWORD_SELECTORS: tuple[str, ...] = (
        "#nloginpwd",
        "input[name='nloginpwd']",
        "input[type='password']",
    )
    _SUBMIT_SELECTORS: tuple[str, ...] = (
        "#loginsubmit",
        "a:has-text('登    录')",
        "a:has-text('登录')",
        "button:has-text('登录')",
        "button[type='submit']",
    )
    _CAPTCHA_SELECTORS: tuple[str, ...] = (
        "#JDJRV-wrap-loginsubmit",
        ".JDJRV-bigimg",
        ".JDJRV-slide-bg",
        "[id*='JDJRV']",
        "[class*='captcha']",
        "iframe[src*='captcha']",
        "text=安全验证",
        "text=向右滑动",
        "text=短信验证码",
        "text=获取验证码",
    )
    _DEFAULT_CONFIRM_SELECTORS: tuple[str, ...] = (
        "button:has-text('确认授权')",
        "button:has-text('同意授权')",
        "button:has-text('授权登录')",
        "button:has-text('立即授权')",
        "button:has-text('允许')",
        "a:has-text('确认授权')",
        "a:has-text('同意授权')",
    )

    def __init__(
        self,
        *,
        cdp_url: str = "",
        headless: bool = True,
        profile_dir: str | Path | None = None,
        timeout_seconds: float = 45.0,
        confirm_selectors: tuple[str, ...] | None = None,
        username: SecretStr | str = "",
        password: SecretStr | str = "",
        poll_interval_seconds: float = 1.5,
        playwright_factory: Callable[[], Any] | None = None,
    ) -> None:
        """初始化浏览器自动续约客户端与安全参数。"""
        self._cdp_url = cdp_url.strip()
        self._headless = headless
        self._profile_dir = Path(profile_dir) if profile_dir else None
        self._timeout_seconds = timeout_seconds
        self._confirm_selectors = (
            confirm_selectors if confirm_selectors is not None else self._DEFAULT_CONFIRM_SELECTORS
        )
        self._username = username if isinstance(username, SecretStr) else SecretStr(username)
        self._password = password if isinstance(password, SecretStr) else SecretStr(password)
        self._poll_interval_seconds = poll_interval_seconds
        self._playwright_factory = playwright_factory

    @classmethod
    def _is_allowed_authorize_host(cls, host: str) -> bool:
        """判断授权链接主机名是否属于京东官方域名或测试保留域。"""
        lowered = host.lower().rstrip(".")
        return lowered in cls._ALLOWED_HOSTS or lowered.endswith((".jd.com", ".jdjygold.com", ".test"))

    def _validate_authorize_url(self, authorize_url: str) -> str:
        """二次校验待打开的授权链接为受信任 HTTPS 地址。"""
        try:
            parsed = httpx.URL(authorize_url)
        except httpx.InvalidURL:
            raise JDGoldBrowserRenewError("INVALID_URL", "京东授权链接格式无效") from None
        if parsed.scheme != "https" or not parsed.host or parsed.username or parsed.password:
            raise JDGoldBrowserRenewError("INVALID_URL", "京东授权链接协议或主机不合法")
        if not self._is_allowed_authorize_host(parsed.host):
            raise JDGoldBrowserRenewError("INVALID_URL", "京东授权链接不属于受信任域名")
        return authorize_url

    @classmethod
    def _is_login_page(cls, url: str) -> bool:
        """判断当前页面是否处于京东账号登录页。"""
        try:
            host = (httpx.URL(url).host or "").lower()
        except httpx.InvalidURL:
            return False
        return any(host == marker or host.endswith(f".{marker}") for marker in cls._LOGIN_HOST_MARKERS)

    def _load_playwright_factory(self) -> Callable[[], Any]:
        """按需加载 Playwright 异步入口，避免未安装时阻断应用导入。"""
        if self._playwright_factory is not None:
            return self._playwright_factory
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise JDGoldBrowserRenewError(
                "PLAYWRIGHT_UNAVAILABLE",
                "未安装 playwright 依赖，请先运行 uv sync 或 uv add playwright",
            ) from None
        return async_playwright

    @staticmethod
    async def _is_selector_visible(page: Any, selector: str) -> bool:
        """安全检测页面中指定选择器是否可见。"""
        try:
            locator = page.locator(selector).first
            return bool(await locator.is_visible(timeout=500))
        except Exception:
            return False

    async def _detect_captcha_or_mfa(self, page: Any) -> bool:
        """检测页面是否出现验证码、滑块或短信二次校验，绝不尝试绕过。"""
        for selector in self._CAPTCHA_SELECTORS:
            if await self._is_selector_visible(page, selector):
                return True
        return False

    async def _click_first_visible(self, page: Any, selectors: tuple[str, ...]) -> bool:
        """按优先级查找首个可见元素并执行单次点击。"""
        for selector in selectors:
            if await self._is_selector_visible(page, selector):
                try:
                    await page.locator(selector).first.click(timeout=3000)
                    return True
                except Exception:
                    continue
        return False

    async def _fill_first_visible(self, page: Any, selectors: tuple[str, ...], value: str) -> bool:
        """按优先级查找首个可见输入框并填入内容。"""
        for selector in selectors:
            if await self._is_selector_visible(page, selector):
                try:
                    await page.locator(selector).first.fill(value, timeout=3000)
                    return True
                except Exception:
                    continue
        return False

    async def _check_first_visible(self, page: Any, selectors: tuple[str, ...]) -> bool:
        """按优先级查找首个可见协议复选框并确保勾选。"""
        for selector in selectors:
            if await self._is_selector_visible(page, selector):
                try:
                    locator = page.locator(selector).first
                    if hasattr(locator, "is_checked") and await locator.is_checked():
                        return True
                    if hasattr(locator, "check"):
                        await locator.check(timeout=3000)
                    else:
                        await locator.click(timeout=3000)
                    return True
                except Exception:
                    continue
        return False

    async def _submit_credentials(self, page: Any) -> bool:
        """在京东登录页使用配置的脱敏账号密码填充并提交登录表单。"""
        username = self._username.get_secret_value().strip()
        password = self._password.get_secret_value()
        if not username or not password:
            raise JDGoldBrowserRenewError(
                "MANUAL_LOGIN_REQUIRED",
                "检测到京东登录页且未配置账号密码，请配置 JD_LOGIN_USERNAME / JD_LOGIN_PASSWORD 或人工登录",
            )
        await self._click_first_visible(page, self._ACCOUNT_TAB_SELECTORS)
        await self._check_first_visible(page, self._AGREE_CHECKBOX_SELECTORS)
        filled_user = await self._fill_first_visible(page, self._USERNAME_SELECTORS, username)
        filled_pass = await self._fill_first_visible(page, self._PASSWORD_SELECTORS, password)
        if not (filled_user and filled_pass):
            return False
        return await self._click_first_visible(page, self._SUBMIT_SELECTORS)

    async def _run_authorization_loop(
        self,
        page: Any,
        authorize_url: str,
        complete_callback: Callable[[], Awaitable[None]],
    ) -> None:
        """导航至授权链接、按需自动登录并轮询等待上游换取正式令牌。"""
        timeout_ms = int(self._timeout_seconds * 1000)
        try:
            await page.goto(authorize_url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            raise JDGoldBrowserRenewError("NAVIGATION_FAILED", "打开京东授权页面失败，请检查网络连接") from None

        deadline = time.monotonic() + self._timeout_seconds
        quick_login_clicked = False
        credentials_submitted = False
        confirm_clicked = False

        while time.monotonic() < deadline:
            if await self._detect_captcha_or_mfa(page):
                raise JDGoldBrowserRenewError(
                    "CAPTCHA_OR_MFA_REQUIRED",
                    "京东登录触发验证码、滑块或短信安全校验，已停止自动续约，请人工在浏览器完成验证",
                )

            current_url = str(getattr(page, "url", "") or "")
            if self._is_login_page(current_url):
                await self._check_first_visible(page, self._AGREE_CHECKBOX_SELECTORS)
                if await self._click_first_visible(page, self._AGREE_PROTOCOL_SELECTORS):
                    quick_login_clicked = False
                elif not quick_login_clicked and await self._click_first_visible(page, self._QUICK_LOGIN_SELECTORS):
                    quick_login_clicked = True
                elif not quick_login_clicked and not credentials_submitted:
                    credentials_submitted = await self._submit_credentials(page)
            else:
                if not confirm_clicked:
                    confirm_clicked = await self._click_first_visible(page, self._confirm_selectors)
                try:
                    await complete_callback()
                    return
                except JDGoldError as error:
                    if error.code == "INVALID_ENVELOPE":
                        raise JDGoldBrowserRenewError("EXCHANGE_INVALID", "京东令牌兑换响应格式异常") from None
                    if error.code == "AUTH_REQUIRED":
                        raise JDGoldBrowserRenewError(
                            "AUTH_SESSION_EXPIRED", "京东登录授权会话已过期，请重新发起授权"
                        ) from None

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(self._poll_interval_seconds, remaining))

        raise JDGoldBrowserRenewError("RENEW_TIMEOUT", "京东浏览器自动续约超时，请检查登录态或改为人工授权")

    async def _launch_persistent_context(self, playwright: Any, *, headless: bool) -> Any:
        """启动权限受限的持久化浏览器上下文，保留京东登录态 Cookie。"""
        if self._profile_dir is None:
            raise JDGoldBrowserRenewError("PROFILE_NOT_CONFIGURED", "未配置 CDP 地址且未指定浏览器 Profile 目录")
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            os.chmod(self._profile_dir, 0o700)
        try:
            return await playwright.chromium.launch_persistent_context(
                str(self._profile_dir),
                channel="chrome",
                headless=headless,
            )
        except Exception:
            try:
                return await playwright.chromium.launch_persistent_context(
                    str(self._profile_dir),
                    headless=headless,
                )
            except Exception:
                raise JDGoldBrowserRenewError(
                    "BROWSER_LAUNCH_FAILED",
                    "启动持久化浏览器失败，请检查本机 Chrome 或 Playwright Chromium 安装状态",
                ) from None

    async def authorize(
        self,
        authorize_url: str,
        complete_callback: Callable[[], Awaitable[None]],
        *,
        headless: bool | None = None,
    ) -> None:
        """执行浏览器自动授权；默认复用本地持久化 Profile，CDP 模式仅关闭本次新建标签页。"""
        validated_url = self._validate_authorize_url(authorize_url)
        factory = self._load_playwright_factory()
        timeout_ms = int(min(self._timeout_seconds, 15.0) * 1000)

        async with factory() as playwright:
            if self._cdp_url:
                try:
                    browser = await playwright.chromium.connect_over_cdp(self._cdp_url, timeout=timeout_ms)
                except Exception:
                    raise JDGoldBrowserRenewError(
                        "CDP_UNAVAILABLE",
                        "无法连接本机 Chrome CDP 调试端口，请确认 Chrome 已使用 --remote-debugging-port 启动",
                    ) from None

                created_context = False
                context = browser.contexts[0] if getattr(browser, "contexts", None) else None
                if context is None:
                    context = await browser.new_context()
                    created_context = True
                page = None
                try:
                    page = await context.new_page()
                    await self._run_authorization_loop(page, validated_url, complete_callback)
                finally:
                    if page is not None:
                        with suppress(Exception):
                            await page.close()
                    if created_context:
                        with suppress(Exception):
                            await context.close()
                return

            effective_headless = self._headless if headless is None else headless
            context = await self._launch_persistent_context(playwright, headless=effective_headless)
            try:
                page = await context.new_page()
                await self._run_authorization_loop(page, validated_url, complete_callback)
            finally:
                with suppress(Exception):
                    await context.close()

    async def interactive_login(
        self,
        authorize_url: str,
        complete_callback: Callable[[], Awaitable[None]],
        *,
        timeout_seconds: float = 300.0,
    ) -> None:
        """打开可见的持久化浏览器窗口供人工完成首次登录与安全校验，完成后自动保存会话与令牌。"""
        validated_url = self._validate_authorize_url(authorize_url)
        factory = self._load_playwright_factory()
        deadline = time.monotonic() + timeout_seconds

        async with factory() as playwright:
            context = await self._launch_persistent_context(playwright, headless=False)
            try:
                pages = getattr(context, "pages", None)
                page = pages[0] if pages else await context.new_page()
                try:
                    await page.goto(validated_url, wait_until="domcontentloaded", timeout=30_000)
                except Exception:
                    raise JDGoldBrowserRenewError("NAVIGATION_FAILED", "打开京东授权页面失败，请检查网络连接") from None

                while time.monotonic() < deadline:
                    if hasattr(page, "is_closed") and page.is_closed():
                        raise JDGoldBrowserRenewError("MANUAL_LOGIN_ABORTED", "浏览器窗口已关闭，未完成京东授权")
                    try:
                        await complete_callback()
                        return
                    except JDGoldError as error:
                        if error.code == "INVALID_ENVELOPE":
                            raise JDGoldBrowserRenewError("EXCHANGE_INVALID", "京东令牌兑换响应格式异常") from None
                        if error.code == "AUTH_REQUIRED":
                            raise JDGoldBrowserRenewError(
                                "AUTH_SESSION_EXPIRED", "京东登录授权会话已过期，请重新发起授权"
                            ) from None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(self._poll_interval_seconds, remaining))
                raise JDGoldBrowserRenewError("RENEW_TIMEOUT", "等待人工完成京东登录授权超时，请重新执行 auth session")
            finally:
                with suppress(Exception):
                    await context.close()
