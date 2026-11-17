# quant-agentic

TODO(init-project): one sentence describing what this project does.

FastAPI backend skeleton with a strict layered dependency direction:

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
