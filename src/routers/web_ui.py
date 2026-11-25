"""Web 前端静态产物托管与 SPA 入口路由。

生产/日常单进程模式下由 FastAPI 直接托管 `web/dist` 构建产物，
不拦截 `/api/*`、`/health`、`/docs`、`/redoc` 与 `/openapi.json`。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

WEB_DIST_DIR: Path = Path(__file__).resolve().parents[2] / "web" / "dist"

router = APIRouter(include_in_schema=False)

_UNBUILT_HTML = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>quant-agentic Web 未构建</title></head>
<body style="font-family: -apple-system, sans-serif; background:#0b0f17; color:#e2e8f0; padding:48px;">
  <h2 style="color:#fbbf24;">quant-agentic Web 工作台尚未构建</h2>
  <p>请先在项目根目录执行以下命令生成前端静态产物，然后刷新本页：</p>
  <pre style="background:#111723; padding:12px 16px; border-radius:8px; display:inline-block;">make web-build</pre>
  <p style="color:#94a3b8; font-size:13px;">
    开发联调模式请访问 <code>http://127.0.0.1:5173</code>；
    API 文档见 <a href="/docs" style="color:#38bdf8;">/docs</a>。
  </p>
</body>
</html>"""


def _serve_index() -> Response:
    index_file = WEB_DIST_DIR / "index.html"
    if not index_file.is_file():
        return HTMLResponse(content=_UNBUILT_HTML, status_code=503)
    return FileResponse(index_file)


@router.get("/")
def web_root() -> Response:
    """根路径托管 Web 量化工作台入口。"""
    return _serve_index()


@router.get("/dashboard")
def web_dashboard() -> Response:
    """工作台主路由。"""
    return _serve_index()


@router.get("/manifest.json")
def web_manifest() -> FileResponse:
    """PWA 应用清单，支持桌面浏览器一键添加为独立窗口。"""
    manifest = WEB_DIST_DIR / "manifest.json"
    if not manifest.is_file():
        raise HTTPException(status_code=404, detail="未找到前端 manifest.json，请先执行 make web-build")
    return FileResponse(manifest, media_type="application/manifest+json")


@router.get("/assets/{asset_path:path}")
def web_assets(asset_path: str) -> FileResponse:
    """托管 Vite 构建生成的静态资源，严格限制在 assets 目录内防止路径穿越。"""
    assets_root = (WEB_DIST_DIR / "assets").resolve()
    target = (assets_root / asset_path).resolve()
    if not target.is_relative_to(assets_root) or not target.is_file():
        raise HTTPException(status_code=404, detail="静态资源不存在")
    return FileResponse(target)
