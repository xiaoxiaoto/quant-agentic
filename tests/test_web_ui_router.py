"""Web 前端静态托管与 SPA 路由测试。"""

from pathlib import Path

from fastapi.testclient import TestClient

from main import app
from src.configs.settings import Settings
from src.routers import web_ui
from src.runtime.dependencies import RuntimeDependencies


def _runtime(tmp_path: Path) -> RuntimeDependencies:
    return RuntimeDependencies(
        Settings(
            data_dir=str(tmp_path),
            timesfm_enabled=False,
            news_enabled=False,
            live_quotes_enabled=False,
        )
    )


def test_web_ui_unbuilt_returns_helpful_503(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web_ui, "WEB_DIST_DIR", tmp_path / "missing_dist")
    app.state.runtime = _runtime(tmp_path)
    try:
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 503
        assert "make web-build" in resp.text
        assert client.get("/health").status_code == 200
    finally:
        app.state.runtime = None


def test_web_ui_serves_built_dist_and_blocks_traversal(tmp_path: Path, monkeypatch) -> None:
    dist_dir = tmp_path / "dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>GoldQuant Web</body></html>", encoding="utf-8")
    (dist_dir / "manifest.json").write_text('{"name":"GoldQuant"}', encoding="utf-8")
    (assets_dir / "app.js").write_text("console.log('ok');", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    monkeypatch.setattr(web_ui, "WEB_DIST_DIR", dist_dir)
    app.state.runtime = _runtime(tmp_path)
    try:
        client = TestClient(app)
        assert client.get("/").status_code == 200
        assert "GoldQuant Web" in client.get("/dashboard").text
        assert client.get("/manifest.json").json()["name"] == "GoldQuant"
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/assets/../../secret.txt").status_code == 404
        assert client.get("/health").status_code == 200
    finally:
        app.state.runtime = None
