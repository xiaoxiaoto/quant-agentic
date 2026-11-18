# quant-agentic

积存黄金研究与影子交易后端，提供行情质量校验、历史时间点回放、启发式决策、独立风控、模拟记账和银行流水对账。

基于 FastAPI，依赖方向如下：

```
(contracts | configs) → repositories → agents → services → runtime → routers
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
| `src/agents/` | LLM orchestration, tools, prompt templates |
| `src/services/` | Business logic |
| `src/runtime/` | DI, middleware, lifespan, scheduler, request context |
| `src/routers/` | HTTP / WebSocket entry points |
| `tests/architecture/` | AST test enforcing the layer contract |
| `agents/` | Progressive-disclosure instructions for coding agents |
| `docs/` | Architecture and decision records |

See [`CLAUDE.md`](CLAUDE.md) for the full standard and [`docs/architecture.md`](docs/architecture.md) for the layer rationale.

## 当前实现边界

- 行情追加存储为本地 JSONL；新记录同时保存原始载荷哈希与完整记录哈希。旧日志缺少完整记录哈希时，无法据此证明所有字段未变。
- `main.py` 为每个应用构建共享依赖，决策、资产查询与对账使用同一个内存账本。线程内成交去重和记账已加锁；重启恢复、多进程共享与持久化成交日志尚未实现，因此当前仅适合单进程研究与模拟。
- 已提供真实 LightGBM 训练与加载、TimesFM 3.0 本地推理、基于 `pydantic-ai` 的新闻结构化提取。未配置 LightGBM 产物时仍使用 `heuristic_v1` 研究模式，返回 `is_calibrated=false`；这不代表真实模型已上线或获得生产准入。
- RFQ 和执行适配器为模拟实现，未连接银行、未预留资金，也不支持部分成交。配置开关不代表真实交易授权。
- 波动率及夏普按实际平均采样间隔年化，不等间隔时仍是近似；ATR 字段使用价格标准差代理。缺少多重检验统计输入，`deflated_sharpe` 返回空值。
- 风控已核验现金、库存、单笔退出损失和配置的最大黄金敞口；日/周累计亏损及周末风险桶参数尚未接入执行门禁。

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

**News LLM**：设置 `NEWS_LLM_ENABLED=true`、`NEWS_LLM_BASE_URL`、`NEWS_LLM_MODEL`、`NEWS_LLM_API_KEY`。服务须支持 OpenAI 兼容 Chat Completions 及 JSON Schema 结构化输出。

```bash
curl -X POST http://localhost:8000/api/v1/models/news/extract \
  -H 'Content-Type: application/json' \
  -d '{"title":"政策新闻","content":"新闻原文","source":"授权新闻源","published_at":"2026-11-18T08:00:00+08:00"}'
```

模型只提取事件类别、严重度、可信度和摘要，不能覆盖来源和时间。通过校验后自动录入共享新闻风险引擎，高风险事件会阻止决策；可用时间从提取完成起计算，防止回放泄漏。上游失败返回 502，未启用返回 503；相同事件 ID 的冲突内容返回 409，修订新闻须使用新 ID。当前不包含新闻源采集，新闻事件仍存于单进程内存。

`GET /api/v1/models/status` 返回模型来源、期限、加载状态和失败原因；`POST /api/v1/decisions/evaluate` 的 `prediction` 返回实际预测版本与校准状态。已配置 LightGBM 推理失败时返回 503，不静默切换启发式。决策历史按来源、产品和可用时点隔离，并与新闻特征共用应用依赖。
