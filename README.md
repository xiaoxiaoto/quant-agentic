# quant-agentic

积存黄金研究与影子交易后端，提供行情质量校验、历史时间点回放、启发式决策、独立风控、模拟记账和银行流水对账。

基于 FastAPI，依赖方向如下：

```
(contracts | configs) → (repositories | clients) → agents → services → runtime → routers
```

## Quickstart

```bash
uv sync                       # install runtime + dev dependencies
cp .env.example .env          # local settings (git-ignored)
make dev                      # uv run uvicorn main:app --reload --port 8000
curl http://127.0.0.1:8000/health
```

## Quality gates

```bash
make check-all                # lint, format, typecheck, architecture, tests
uv run lint-imports           # layered architecture contract
uv run pytest tests/architecture/
```

## Layout

| Path | Contents |
| --- | --- |
| `main.py` | Composition root — the only file allowed to import every layer |
| `src/contracts/` | Domain types, Pydantic v2 schemas, enums, domain exceptions |
| `src/configs/` | Settings, env, constants, logging config |
| `src/repositories/` | ORM models and data access |
| `src/clients/` | 京东外部接口客户端，与 repositories 同级且互不依赖 |
| `src/agents/` | LLM orchestration, tools, prompt templates |
| `src/services/` | Business logic |
| `src/runtime/` | DI, middleware, lifespan, scheduler, request context |
| `src/routers/` | HTTP / WebSocket entry points |
| `tests/architecture/` | AST test enforcing the layer contract |
| `agents/` | Progressive-disclosure instructions for coding agents |
| `docs/` | Architecture and decision records |

See [`CLAUDE.md`](CLAUDE.md) for the full standard and [`docs/architecture.md`](docs/architecture.md) for the layer rationale.

## 当前实现边界

- 双边报价与采集到的多品种参考行情统一建表持久化到 `DATA_DIR/quant-agentic.sqlite3`（SQLite WAL 模式：`quotes`、`reference_quotes`），并保留按日 JSONL 审计日志与哈希完整性校验；历史 JSONL 文件查询时会自动幂等同步入库。
- 运行时状态（`ledger_orders`、`alerts`、`batch_instructions`、`control_state`、`news_events`、`macro_events`、`reconciliation_reports`）已拆分为独立 SQLite 规范表，支持重启恢复与增量落盘，兼容迁移旧版 `runtime_state` KV 数据。当前仍面向单机 SQLite 部署，不支持多机分布式协同。
- 已提供真实 LightGBM 训练与加载、TimesFM 3.0 本地推理、基于 `pydantic-ai` 的新闻结构化提取。未配置 LightGBM 产物时仍使用 `heuristic_v1` 研究模式，返回 `is_calibrated=false`；这不代表真实模型已上线或获得生产准入。
- RFQ 和执行适配器为模拟实现，未连接银行、未预留资金，也不支持部分成交。配置开关不代表真实交易授权。模拟盘支持可选开启 `SYNTHETIC_PAPER_QUOTE_ENABLED=true` 将 `CNY/g` 参考价按配置点差合成为 `is_synthetic=true` 报价，实盘决策强制排除所有合成报价。
- 波动率及夏普按实际平均采样间隔年化，不等间隔时仍是近似；ATR 字段使用价格标准差代理。缺少多重检验统计输入，`deflated_sharpe` 返回空值。
- 风控已核验现金、库存、单笔退出损失、最大黄金敞口，以及日/周累计亏损熔断（`NO_NEW_RISK` / `REDUCE_ONLY`）和信号冷却期；周末风险桶上限参数仍需结合实盘回测进一步校准。对账支持 `POST /api/v1/reconciliation/jd-holdings` 自动映射京东实盘持仓并持久化审计报告。

## 真实模型接入

所有依赖用 `uv sync` 安装。数值模型使用官方 LightGBM / TimesFM 库，新闻大模型使用 Pydantic AI Agent 和严格的 Pydantic 输出类型。配置项见 `.env.example`；密钥只写入本地 `.env`。TimesFM 和 News LLM 默认开启，启动前必须配置新闻模型名和密钥，缺失时明确报错；离线研究可在 `.env` 显式关闭两个开关。

**LightGBM**：macOS 上使用 CPU 训练和推理。官方 LightGBM 没有 Apple MPS 后端。[官方安装文档](https://lightgbm.readthedocs.io/en/stable/Installation-Guide.html)

从已完成点时特征和扣费净收益标签的 JSONL 训练，每行格式如下（`features` 为 `FeatureSet`，允许补充其余特征）：

```json
{"features":{"quote_id":"q1","as_of":"2025-01-01T00:00:00Z","return_1d":0.01},"horizon":"1w","net_return":0.005,"label_end_at":"2025-01-08T00:00:00Z"}
```

```bash
uv run python -m src.agents.lightgbm --input data/training/samples.jsonl --output data/models/lightgbm/v1
```

每期限至少 80 条递增时间样本，训练与校准集均须包含盈利和亏损样本。后 25% 用于概率和残差下界校准，清除跨分割标签后训练集至少 40 条；周级重叠标签可能需要更多数据。设置 `LIGHTGBM_MODEL_DIR=data/models/lightgbm/v1` 后启动服务。决策接口使用 `1w`，产物必须包含该期限；影子回放还需要 `1d`。推理时间必须晚于校准标签结束时间，模型直接预测净收益，不重复扣费。校准集 Brier 只表示校准内诊断，仍需独立样本外评估。

**TimesFM 3.0**：默认 `TIMESFM_ENABLED=true`；首次有效请求懒加载 `TIMESFM_CHECKPOINT` 权重。输入按小时进行因果采样，至少需要 32 个连续小时样本，长缺口截断上下文；因此周末后可能暂时不可用。`TIMESFM_DEVICE=cpu` 可在本机使用。启用后缺少有效特征的决策请求返回 503；影子运行对上下文不足记录预热弃权，推理错误则中止，不再使用动量冒充模型预测。下载权重仅限其许可证允许的非商业、非生产用途；生产使用需另行核实授权部署方式。[官方许可说明](https://github.com/google-research/timesfm#license-notice-for-pretrained-weights)

**News LLM**：设置 `OPENAI_BASE_URL`、`OPENAI_MODEL`、`OPENAI_API_KEY` 以及业务开关 `NEWS_ENABLED=true`。服务须支持 OpenAI 兼容 Chat Completions 及 JSON Schema 结构化输出。未配置模型与密钥时本地可通过 `NEWS_ENABLED=false` 保持免凭据开箱运行。

```bash
curl -X POST http://localhost:8000/api/v1/models/news/extract \
  -H 'Content-Type: application/json' \
  -d '{"title":"政策新闻","content":"新闻原文","source":"授权新闻源","published_at":"2026-11-18T08:00:00+08:00"}'
```

模型只提取事件类别、严重度、可信度和摘要，不能覆盖来源和时间。通过校验后自动录入共享新闻风险引擎，高风险事件会阻止决策；可用时间从提取完成起计算，防止回放泄漏。上游失败返回 502，未启用返回 503；相同事件 ID 的冲突内容返回 409，修订新闻须使用新 ID。当前不包含新闻源采集，新闻事件仍存于单进程内存。

`GET /api/v1/models/status` 返回模型来源、期限、加载状态和失败原因；`POST /api/v1/decisions/evaluate` 的 `prediction` 返回实际预测版本与校准状态。已配置 LightGBM 推理失败时返回 503，不静默切换启发式。决策历史按来源、产品和可用时点隔离，并与新闻特征共用应用依赖。

## 京东客户端与实时参考行情

`src/clients/jd_gold.py` 统一封装 jdgold 的 31 个接口：授权、行情、持仓、早报、快讯、订单、条件单、收益日历、模拟交易、贵金属行情/K 线、资讯、黄金分析和达人排行。`clients` 与 `repositories` 同层且互不依赖，均由服务层调用。原技能中的本地代理、升级下载和浏览器展示不属于业务 API，未迁入应用。

客户端使用注入的 `httpx.AsyncClient`，连接由调用方关闭；应用使用 `runtime.jd_gold` 整合客户端和 SQLite 授权仓储，所有业务接口通过 `runtime.jd_gold.client` 调用。登录权限如下：

| 客户端方法 | 访问要求 |
| --- | --- |
| `holdings`、`morning_report`、`order_list`、`order_sum`、`conditional_orders`、`conditional_order_detail`、`income_calendar` | 京东账户登录授权 |
| 所有 `sim_*` 方法（含模拟行情、账户及买卖） | 京东账户登录授权 |
| `query_price`、`news_flash`、`stock_quote`、`stock_kline`、`news`、`latest_price` | 匿名请求，即使已登录也不发送账户令牌 |
| 所有 `gold_*` 方法、`fund_ranking` | 无需账户登录，但须配置 `JD_GATEWAY_API_KEY` |
| `auth_login_url`、`auth_status`、`auth_exchange` | 授权流程本身无需账户凭据 |

受保护查询缺少有效凭据时自动生成 PKCE 授权链接，抛出 `JDGoldLoginRequiredError`；调用方读取其 `authorize_url.get_secret_value()`，仅用于向用户展示或打开授权页面。并发请求复用十分钟内的待完成会话。用户确认浏览器授权完成后，显式调用 `await client.complete_login()`，再重新发起原查询；应用共享实例通过注入的授权仓储将凭据和待授权会话保存到 `DATA_DIR/quant-agentic.sqlite3`，重启后恢复，后续请求自动携带。直接构造客户端时可传入 `auth_store`；不传则仅存内存。后端 `expiresIn` 为毫秒，旧凭据可用至实际到期；过期后继续使用已有待授权会话，或自动准备新的授权链接。此流程不需要手工配置 `JD_ACCESS_TOKEN`，该配置仍可用于注入外部凭据（无法预知其过期时间，上游 401 后清除）。

```python
from src.clients.jd_gold import JDGoldLoginRequiredError

try:
    result = await client.holdings()
except JDGoldLoginRequiredError as error:
    authorize_url = error.authorize_url.get_secret_value()
    # 将链接交给用户打开，不写入日志。
```

收到用户授权成功确认后，在同一客户端实例上继续：

```python
await client.complete_login()
result = await client.holdings()
```

真实账号授权可在与用户浏览器同机的终端执行：

```bash
uv run python -m src.runtime.jd_auth login
# 用户完成浏览器授权并确认成功后，再执行兑换。
uv run python -m src.runtime.jd_auth complete
uv run python -m src.runtime.jd_auth verify
uv run python -m src.runtime.jd_auth status
```

命令行和应用通过 SQLAlchemy ORM 访问同一个 SQLite 文件。已有 `jd_auth.sqlite3` 的环境，停止应用和授权命令后将文件改名为 `quant-agentic.sqlite3` 即可保留登录态。请勿覆盖已有的同名数据库。数据库权限为 `0600`，内容为未加密的登录凭据，仅保存在 Git 忽略的数据目录；应按密钥文件管理，不上传或分享。每个上游地址仅保存一个账户登录态；应用目前按单进程运行，不支持多个账户并行授权。待授权会话十分钟过期，兑换成功后清除会话参数；401 清除持久化凭据。配置 `JD_ACCESS_TOKEN` 会在新建客户端时覆盖已保存凭据，浏览器授权模式应保持该配置为空。

仓储启动时会将当前上游在旧表 `jd_auth` 中的登录态补入 `jd_gold_auth`，已有的新表记录优先。应用运行时每 15 秒检查 SQLite 登录态。进入到期前五分钟时，服务自动准备待授权会话、保存到 SQLite 并写出提示。此时 `status` 会显示 `pending_login=true`，运行 `login` 可在本机打开该授权页面。京东黄金授权代理不下发 `refresh_token`，仍需本人完成授权后执行 `complete`。设置 `JD_PROACTIVE_AUTH_ENABLED=false` 可关闭后台检查，缺少有效凭据时的按需授权仍可用。

客户端不自动打开服务端浏览器，也不在后台轮询兑换；命令行 `login` 才负责打开本机浏览器。不复制技能内置密钥；登录授权不会提供分析网关密钥。401 清除登录态，403 保留受限错误，不重试或自动重放交易；模拟交易没有挂到应用路由或后台任务。已完成真实账号授权、SQLite 跨进程恢复与持仓查询权限验证；其余需授权业务接口依据原技能契约迁移，已做模拟测试，尚未逐一远端验收；公开参考行情的远端验证情况见下文。

默认 `LIVE_QUOTES_ENABLED=true`，服务启动后后台轮询全部 29 个参考行情品种：民生、兴业、广发、浙商、工商、中信、浦发 7 家银行积存金，京东金价指数，伦敦金、伦敦银、现货铂金、现货钯金，以及 17 个上金所黄金、白银、铂金品种。完整编码、名称、单位和价格方向由 `GET /api/v1/quotes/live/products` 返回。采集限制为最多 3 个并发请求，每轮结束后默认等待 10 秒；关闭服务时停止轮询并释放连接。可通过 `.env` 的 `LIVE_QUOTES_INTERVAL_SECONDS` 调整等待时间（5～300 秒），`JD_TIMEOUT_SECONDS` 控制单次超时。测试进程显式关闭外部采集。

`live_quotes_products` 为可选元组配置：`LIVE_QUOTES_PRODUCTS=null` 默认轮询全部品种，也可使用 JSON 数组限定后台采集子集。例如：

```dotenv
LIVE_QUOTES_PRODUCTS=["CMBC-JCJ","CZB-JCJ","SGE-Au99.99","WG-XAUUSD"]
```

单品种接口保留原有路径，增加 `product_code` 查询参数，省略时仍为民生积存金 `CMBC-JCJ`。全量接口读取或采集全部支持品种，不受后台子集配置限制：

```bash
curl http://127.0.0.1:8000/api/v1/quotes/live/products
curl -X POST 'http://127.0.0.1:8000/api/v1/quotes/live/collect?product_code=CZB-JCJ'
curl 'http://127.0.0.1:8000/api/v1/quotes/live/latest?product_code=CZB-JCJ'
curl 'http://127.0.0.1:8000/api/v1/quotes/live/status?product_code=CZB-JCJ'
curl -X POST http://127.0.0.1:8000/api/v1/quotes/live/collect/all
curl http://127.0.0.1:8000/api/v1/quotes/live/latest/all
curl http://127.0.0.1:8000/api/v1/quotes/live/status/all
```

`POST /live/collect/all` 返回 `list[LiveQuoteStatus]`，每个品种独立记录成功时间、失败次数及错误；单个源失败不影响其他品种。`GET /live/status/all` 返回全部品种状态，`GET /live/latest/all` 仅返回已有记录的品种。后台子集之外的品种仍可手动采集，其状态中的 `running` 为 `false`。

参考价返回 `price`、原始单位 `unit`、源 `quote_at`、本地 `received_at`、价格方向 `price_side`、质量状态及载荷哈希。上金所白银使用 `CNY/kg`，国际贵金属使用 `USD/oz`，其余品种使用 `CNY/g`，不自动换算。兴业、广发、浦发的 `price_side=buy` 表示来源标注的买入价口径，仍不代表可成交报价。

民生专用单价入口继续严格拒绝缺少有效源时间、演示数据和产品不匹配的响应。其他行情若上游未提供有效源时间，保留 `quote_at=null` 并标记 `GAP`、`available_at=null`，允许展示参考价格，但不能判断报价时效或用于交易。具备源时间的陈旧和未来报价分别标为 `STALE`、`ANOMALY`。查询时重算时效，动态状态改变后不沿用原日志的记录哈希。各品种独立去重、保存及重启恢复，上游失败不覆盖最后保存值，并在下一轮重新尝试。

2026-11-18 公开行情实测中，29 个品种有 28 个返回价格，浦发积存金返回业务错误 `10009999`。银行、京东指数及国际贵金属的通用行情响应缺少有效源时间，上金所返回的部分源时间陈旧，分别按 `GAP`、`STALE` 展示；支持查询和采集不表示全部品种当前都有有效实时数据。

参考价独立保存到 `DATA_DIR/references/references_YYYY-MM-DD.jsonl`，可在重启后读取并校验完整性，兼容此前民生日志；原始接收时间不会改写。该入口没有银行双边价，返回固定 `is_tradable=false`，不写入交易报价仓储，也不参与现有交易决策。当前为单进程轮询，多 worker 会各自采集。

本地 HTTP 故障模拟（Postman CLI，无需登录）：

```bash
postman simulation run postman/simulations/jd-live.sim.yaml --port 4010 --output ndjson --no-history
```

将 `JD_LATEST_PRICE_URL` 指向 `http://127.0.0.1:4010/jd/latestPrice` 后启动应用；向 `http://127.0.0.1:4010/jd/control/error`、`/control/invalid`、`/control/slow`、`/control/stale` 或 `/control/normal` 发送 POST 可切换故障。`slow` 延迟 2 秒，验证超时时将 `JD_TIMEOUT_SECONDS` 设为 1。模拟器仅建模公开单价接口，不表示真实平台的可用性或错误响应保证。
