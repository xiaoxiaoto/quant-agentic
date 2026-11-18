# quant-agentic

积存黄金研究与影子交易后端，提供行情质量校验、历史时间点回放、启发式决策、独立风控、模拟记账和银行流水对账。

基于 FastAPI，依赖方向如下：

```
types → config → repositories → services → agents → runtime → routers
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
| `src/types/` | Domain types, Pydantic v2 schemas, enums, domain exceptions |
| `src/config/` | Settings, env, constants, logging config |
| `src/repositories/` | ORM models and data access |
| `src/services/` | Business logic |
| `src/agents/` | LLM orchestration, tools, prompt templates |
| `src/runtime/` | DI, middleware, lifespan, scheduler, request context |
| `src/routers/` | HTTP / WebSocket entry points |
| `tests/architecture/` | AST test enforcing the layer contract |
| `agents/` | Progressive-disclosure instructions for coding agents |
| `docs/` | Architecture and decision records |

See [`CLAUDE.md`](CLAUDE.md) for the full standard and [`docs/architecture.md`](docs/architecture.md) for the layer rationale.

## 当前实现边界

- 行情追加存储为本地 JSONL；新记录同时保存原始载荷哈希与完整记录哈希。旧日志缺少完整记录哈希时，无法据此证明所有字段未变。
- `main.py` 为每个应用构建共享依赖，决策、资产查询与对账使用同一个内存账本。线程内成交去重和记账已加锁；重启恢复、多进程共享与持久化成交日志尚未实现，因此当前仅适合单进程研究与模拟。
- 预测器使用固定权重启发式，各期限暂共用打分公式；胜率未经过样本外校准。`TimesFMAdapter` 默认关闭，启用后仍只是动量代理，尚未连接真实模型。
- RFQ 和执行适配器为模拟实现，未连接银行、未预留资金，也不支持部分成交。配置开关不代表真实交易授权。
- 波动率及夏普按实际平均采样间隔年化，不等间隔时仍是近似；ATR 字段使用价格标准差代理。缺少多重检验统计输入，`deflated_sharpe` 返回空值。
- 风控已核验现金、库存、单笔退出损失和配置的最大黄金敞口；日/周累计亏损及周末风险桶参数尚未接入执行门禁。
