"""京东真实账户授权入口，与应用共享 SQLite 登录态。

CLI 解析（argparse / SystemExit）集中在此文件；纯业务逻辑由 JDGoldGateway 承载。
"""

import argparse
import asyncio
import json
import webbrowser

import httpx

from src.clients.jd_gold import JDGoldError
from src.configs.settings import Settings
from src.contracts.domains.jd_auth import JDGoldBrowserRenewError
from src.services.jd_gold.gateway import JDGoldGateway


class JDGoldAuthCommand:
    """CLI 入口：解析操作参数并执行京东账户授权。"""

    @staticmethod
    async def _run(action: str, *, headed: bool = False) -> int:
        """执行单次授权命令，返回进程退出码。"""
        settings = Settings()
        async with httpx.AsyncClient(timeout=settings.jd_timeout_seconds) as http:
            service = JDGoldGateway(http, settings)
            if action == "status":
                summary = await service.auth.status_summary()
                print(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False))
                return 0
            try:
                if action == "login":
                    url = await service.login()
                    if await asyncio.to_thread(webbrowser.open, url.get_secret_value()):
                        print("已打开京东授权页面，请在浏览器完成授权。")
                    else:
                        print("浏览器未能自动打开，请打开以下京东授权链接：")
                        print(url.get_secret_value())
                elif action == "complete":
                    await service.complete_login()
                    print("登录成功，登录态已保存。")
                elif action == "verify":
                    await service.verify_auth()
                    print("账户查询权限验证成功。")
                elif action == "renew":
                    await service.auto_renew(headless=False if headed else None)
                    print("京东账号浏览器自动续约成功，登录态已更新。")
                elif action == "session":
                    print("正在打开持久化浏览器窗口，请在浏览器中完成京东登录与授权（完成后窗口将自动关闭）…")
                    await service.interactive_browser_login()
                    print("京东登录与持久化浏览器会话已保存，后续到期前将自动续约。")
            except JDGoldBrowserRenewError as error:
                print(f"京东浏览器自动续约未完成（{error.code}）：{error.message}")
                return 1
            except JDGoldError as error:
                print(f"京东授权操作未完成（{error.code}），请检查登录状态或重新授权。")
                return 1
        return 0

    @classmethod
    async def run_action(cls, action: str, *, headed: bool = False) -> int:
        """执行指定京东账户授权动作，供统一 CLI 主入口复用。"""
        return await cls._run(action, headed=headed)

    @classmethod
    def main(cls) -> None:
        """解析授权操作；complete 仅供收到用户授权成功确认后执行。"""
        parser = argparse.ArgumentParser(description="京东黄金账户授权")
        parser.add_argument("action", choices=("login", "complete", "status", "verify", "renew", "session"))
        parser.add_argument("--headed", action="store_true", help="使用有头浏览器模式执行自动续约（排障或首次登录）")
        args = parser.parse_args()
        raise SystemExit(asyncio.run(cls.run_action(args.action, headed=bool(args.headed))))


if __name__ == "__main__":
    JDGoldAuthCommand.main()
