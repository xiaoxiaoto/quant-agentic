"""京东真实账户授权入口，与应用共享 SQLite 登录态。

CLI 解析（argparse / SystemExit）集中在此文件；纯业务逻辑由 JDGoldGateway 承载。
"""

import argparse
import asyncio
import json
import time
import webbrowser

import httpx

from src.clients.jd_gold import JDGoldError
from src.configs.settings import Settings
from src.services.jd_gold.gateway import JDGoldGateway


class JDGoldAuthCommand:
    """CLI 入口：解析操作参数并执行京东账户授权。"""

    @staticmethod
    async def _run(action: str) -> int:
        """执行单次授权命令，返回进程退出码。"""
        settings = Settings()
        async with httpx.AsyncClient(timeout=settings.jd_timeout_seconds) as http:
            service = JDGoldGateway(http, settings)
            client = service.client
            if action == "status":
                state = await service.auth.auth_state_async()
                now = time.time()
                actual_expiry = state.actual_expires_at
                remaining = max(0, int(actual_expiry - now)) if actual_expiry is not None else None
                print(
                    json.dumps(
                        {
                            "logged_in": bool(state.access_token.get_secret_value())
                            and (remaining is None or remaining > 0),
                            "usable_seconds": remaining,
                            "refresh_supported": False,
                            "pending_login": bool(state.login_state) and state.login_expires_at > now,
                        }
                    )
                )
                return 0
            try:
                if action == "login":
                    url = await client.login()
                    if await asyncio.to_thread(webbrowser.open, url.get_secret_value()):
                        print("已打开京东授权页面，请在浏览器完成授权。")
                    else:
                        print("浏览器未能自动打开，请打开以下京东授权链接：")
                        print(url.get_secret_value())
                elif action == "complete":
                    await client.complete_login()
                    print("登录成功，登录态已保存。")
                elif action == "verify":
                    await client.holdings()
                    print("账户查询权限验证成功。")
            except JDGoldError as error:
                print(f"京东授权操作未完成（{error.code}），请检查登录状态或重新授权。")
                return 1
        return 0

    @classmethod
    async def run_action(cls, action: str) -> int:
        """执行指定京东账户授权动作，供统一 CLI 主入口复用。"""
        return await cls._run(action)

    @classmethod
    def main(cls) -> None:
        """解析授权操作；complete 仅供收到用户授权成功确认后执行。"""
        parser = argparse.ArgumentParser(description="京东黄金账户授权")
        parser.add_argument("action", choices=("login", "complete", "status", "verify"))
        raise SystemExit(asyncio.run(cls.run_action(parser.parse_args().action)))


if __name__ == "__main__":
    JDGoldAuthCommand.main()
