# dev/

存放本地运维部署模板（`quant-agentic.plist` / `quant-agentic.service`）及一次性开发实验脚本。本目录不属于应用运行路径，禁止被 `src/`、`main.py` 或 `tests/` 导入。

使用说明：

- 常驻服务模板（launchd / systemd）在启用前请将占位路径与运行用户替换为实际环境值。
- 一次性实验脚本请使用 `uv run python dev/<script>.py` 运行，用完后及时清理。
