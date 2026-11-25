# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project — quant-agentic

积存黄金研究与影子交易后端：行情校验、PIT 回放、启发式决策、风控、模拟记账与对账。当前能力边界见 README。

## Commands

All commands use `uv`. Never use `pip`, `poetry`, or hand-edit `pyproject.toml` for dependencies.

- `make dev` — start the dev server: `uv run uvicorn main:app --reload --port 8000`.
- `uv run python main.py` — run the composition root directly (port 8000, reload enabled).
- `make check-all` — lint, format, typecheck, architecture gates, and tests in one shot.

Quality gates while iterating:

```bash
uv run ruff check         # lint
uv run ruff format        # format
uv run pyright            # type-check
uv run lint-imports       # layered architecture contract
uv run pytest             # full test suite
uv run pytest tests/architecture/   # only the architecture test
```

## Engineering discipline

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Python naming conventions

Follow these names for new Python code:

| Type | Public | Internal |
| --- | --- | --- |
| Modules | `lower_with_under` | `_lower_with_under` |
| Packages | `lower_with_under` | |
| Classes | `CapWords` | `_CapWords` |
| Exceptions | `CapWords` | |
| Functions | `lower_with_under()` | `_lower_with_under()` |
| Global/Class Constants | `CAPS_WITH_UNDER` | `_CAPS_WITH_UNDER` |
| Global/Class Variables | `lower_with_under` | `_lower_with_under` |
| Instance Variables | `lower_with_under` | `_lower_with_under` protected, `__lower_with_under` private |
| Method Names | `lower_with_under()` | `_lower_with_under()` protected, `__lower_with_under()` private |
| Function/Method Parameters | `lower_with_under` | |
| Local Variables | `lower_with_under` | |

## Python code organization

All non-exempt Python modules must manage code with classes.
Code comments and docstrings must be written in Chinese (代码注释与文档字符串必须使用中文)，且注释中不得存在（§9.2）、G-1 等计划文档条款编号。

Exempt paths:

- `main.py`
- `src/routers/**`
- `tests/**`

Class bodies must be ordered by member category:

1. Private variables: class-body assignments to `_name` or `__name`
2. Public variables: class-body assignments to names without a leading underscore
3. Private methods: `def _name` or `def __name`
4. Public methods: `def name`

For non-exempt modules, avoid top-level business functions. Put behavior on classes and keep module-level code limited to imports, constants, type aliases, and compatibility wrappers when they are truly needed.

## Architecture — layered dependency direction

This project follows a strict layered architecture. Each layer may import any layer to its left; reverse imports are forbidden and are caught by `import-linter` and the AST test under `tests/architecture/`.

```
(contracts | configs) → (repositories | clients) → agents → services → runtime → routers
```

| Layer | May import | Purpose |
| --- | --- | --- |
| `contracts` | (nothing in `src/`) | Domain types, Pydantic v2 schemas, enums, domain exceptions |
| `configs` | (nothing in `src/`) | Settings (Pydantic Settings), env, constants, logging config |
| `repositories` | `contracts`, `configs` | SQLAlchemy ORM models + DB access |
| `clients` | `contracts`, `configs` | External HTTP clients; sibling of repositories, mutually isolated |
| `agents` | `contracts`, `configs`, `repositories`, `clients` | Decision / orchestration (LLM tools, planners) |
| `services` | `contracts`, `configs`, `repositories`, `clients`, `agents` | Business logic |
| `runtime` | all above | Shared execution primitives — DI providers, middleware, lifespan, scheduler, request context. **Must not import `routers`.** |
| `routers` | all above | HTTP / WebSocket entry points. **Not imported by any layer** — only mounted by `main.py`. |
| `main.py` | everything | Composition root, lives outside `src/`. Only file allowed to wire every layer. |

### Rules when writing or modifying code

1. **Identify the target layer first.** If a change spans layers, plan the cuts up front.
2. **Every new import must respect the table above.** If a needed dependency points "upward", invert it — inject via parameter, define an interface in `types`, or move the shared piece to a layer below. Never relax the contract.
3. **Run `uv run lint-imports` and `uv run pytest tests/architecture/` after editing.** A failure means the design is wrong, not the tool.
4. **`main.py` is the only file that may import every layer.** It is the composition root, deliberately placed outside `src/` so the contract can declare "no `src/` package imports `routers`" without exception.

## Configuration

- Settings live in `src/configs/settings.py`: `Settings(AppSettings, ServerSettings, TradingSettings, ModelSettings, JDSettings)` composes the setting groups, so all fields land flat on `Settings`.
- Values are read from environment variables, falling back to the git-ignored `.env` file (loaded relative to the working directory).
- Env names are the field names in caps: `NAME`, `HOST`, `PORT`, `DEBUG`. A field added to a group is readable as `settings.<field>`.
- `.env.example` is the committed contract: every new field gets a default in its group **and** a line there.
- `main.py` constructs `Settings()` once and wires `settings.name` / `settings.host` / `settings.port` / `settings.debug`.
- Never hardcode values that belong to configuration, and never instantiate a group class directly; read everything through `Settings`.
- Keep `src/configs/__init__.py` empty: a non-empty non-exempt module must be class-managed, so import `from src.configs.settings import Settings` instead of re-exporting.
- `main.py` creates `RuntimeDependencies(settings)` once per application. Routers obtain it through FastAPI dependencies; never construct independent ledgers inside routers.

## External systems

京东接口统一位于 `src/clients/jd_gold.py`，与 `src/repositories` 同层且互不依赖。`JDGoldGateway` 整合共享客户端和 SQLite 授权仓储；应用生命周期默认轮询全部 29 个公开参考行情品种（7 家银行积存金、京东指数、4 种国际贵金属、17 种上金所品种），最多 3 请求并发，每轮完成后等待配置周期，独立存储于 `DATA_DIR/references`，不构造双边报价、不接入交易决策。账户授权状态通过 contracts 层存储协议注入，由 `JDGoldAuthRepository` 保存到 `DATA_DIR/quant-agentic.sqlite3`；CLI `uv run python -m src.runtime.jd_auth login|complete|status|verify` 与应用共享登录态。服务运行时在到期前五分钟自动准备重新授权会话，当前上游没有续期能力，用户仍须完成浏览器授权。分析接口仍需独立密钥，模拟交易仅显式调用。

参考品种、单位及价格方向统一由 `ReferenceProductCatalog` 定义。`live_quotes_products: tuple[str, ...] | None` 对应 `LIVE_QUOTES_PRODUCTS=null`（默认全部）或 JSON 数组（后台子集）；未知品种启动报错。`GET /api/v1/quotes/live/products` 返回完整清单，原 `/live/latest`、`/live/status`、`/live/collect` 接口均支持 `product_code` 查询参数，默认 `CMBC-JCJ`。新增 `/live/latest/all`、`/live/status/all` 与 `POST /live/collect/all`，全量采集返回 `list[LiveQuoteStatus]`，单个品种失败不阻断其余品种。后台子集配置不限制手动全量或单品种采集。

源时间缺失时不得用接收时间代替：通用行情保留 `quote_at=null`，标记 `GAP`、`available_at=null`，仅供展示；民生专用单价入口继续严格拒绝缺失或无效源时间。具备源时间的陈旧报价标记 `STALE`，查询时重新分类。SGE 白银单位 `CNY/kg`，国际贵金属 `USD/oz`，其余 `CNY/g`；兴业、广发、浦发 `price_side=buy` 仍为不可成交单边参考价。按品种分别去重和恢复，默认方向哈希兼容旧民生日志。2026-11-18 实测 29 个品种中 28 个返回价格，浦发业务错误 `10009999`，通用行情缺有效源时间，部分 SGE 时间陈旧；不可将支持接入等同于全部实时可用。

双边行情、多品种参考行情与运行时业务状态（订单、提醒、批量指令、风控/冷却状态、新闻与宏观事件、对账报告、京东账户授权）统一建表保存在 `DATA_DIR/quant-agentic.sqlite3`（SQLite WAL 模式），同时保留按日 JSONL 审计日志；没有外部缓存、队列或真实银行直连。模拟盘可通过 `SYNTHETIC_PAPER_QUOTE_ENABLED=true` 启用参考价合成双边报价（`is_synthetic=true`），实盘决策强制排除合成报价。真实模型与 Agent 入口为 `LightGBMAgent`（训练/加载，位于 `src/agents/lightgbm.py`）、`TimesFMAgent`（官方 TimesFM3 懒加载，位于 `src/agents/timesfm.py`）和 `NewsAgent`（Pydantic AI Agent，位于 `src/agents/news.py`）。模型配置通过 `Settings` 注入应用共享依赖；TimesFM 与 News LLM 默认开启，缺新闻模型配置时启动报错，无 LightGBM 产物时是显式标识的启发式研究模式。不可将适配器已实现等同于真实凭据、生产训练产物或生产授权已就绪。

## Runtime entry points

- `main.py` — composition root; builds the FastAPI app, mounts routers, runs uvicorn on `0.0.0.0:8000`.
- `main:app` — ASGI target for `uvicorn` and deployments.
- `src/routers/health.py` — `GET /health`.
- `make dev` / `make check-all` — Makefile wrappers for the above.

## Agent instructions

This project also follows the [`AGENTS.md`](https://agents.md/) convention:

- `AGENTS.md` is the project-wide coding-agent entrypoint.
- `agents/` contains progressive-disclosure agent guides. Load only the relevant nested `AGENT.md`.
- `agents/` is documentation for coding agents; runtime orchestration code lives in `src/agents/`.

## Project-specific glossary

- **PIT（Point-in-Time）**：按 `available_at <= decision_at` 过滤历史数据，杜绝前视偏差。
- **RFQ（Request for Quote）**：报价接受成交模型，以银行双边报价（`bid_price` / `ask_price`）而非交易所撮合模拟成交。
- **参考行情（ReferenceQuote）**：单边参考价（`is_tradable=False`），独立存于 `DATA_DIR/references`，不进入交易决策。
- **影子账本（ShadowLedger）**：本地内存账本与持仓成本核算引擎，以银行账单为权威并通过对账保持一致。

## Architecture standard

This project follows the layered dependency direction maintained by the [`init-project`](~/.agent/skills/init-project/SKILL.md) skill. Re-run `/init-project` to refresh this document or audit for violations.
