# Architecture

## Dependency direction

Arrows point in the allowed import direction. A layer may import any layer to its left; reverse imports fail the build.

```
types → config → repositories → services → agents → runtime → routers
```

| Layer | May import | Purpose |
| --- | --- | --- |
| `src/types` | (nothing in `src/`) | Domain types, Pydantic v2 schemas, enums, domain exceptions |
| `src/config` | `types` | Settings (Pydantic Settings), env, constants, logging config |
| `src/repositories` | `types`, `config` | SQLAlchemy ORM models + DB access |
| `src/services` | `types`, `config`, `repositories` | Business logic |
| `src/agents` | `types`, `config`, `repositories`, `services` | Decision / orchestration (LLM tools, planners) |
| `src/runtime` | all above | DI providers, middleware, lifespan, scheduler, request context |
| `src/routers` | all above | HTTP / WebSocket entry points |
| `main.py` | everything | Composition root; lives outside `src/` |

Two structural facts make the contract total:

- `main.py` lives outside `src/`, so "no `src/` package imports `routers`" needs no exception.
- `runtime` must never import `routers`; the `layers` contract forbids it, which keeps execution primitives usable by background workers, not just HTTP requests.

## Enforcement

| Mechanism | Command | Catches |
| --- | --- | --- |
| `import-linter` (`layers` contract in `.importlinter`) | `uv run lint-imports` | Static reverse imports at module scope |
| AST test `tests/architecture/test_layers.py` | `uv run pytest tests/architecture/` | Imports inside functions / `TYPE_CHECKING`, plus class-managed module organization |

Both run in `make arch-check` and `make check-all`. A failure means the design is wrong, not the tool: invert the dependency (parameter injection, an interface in `types`, or move the shared piece down) rather than adding an `ignore_imports` entry.

`ignore_imports` is reserved for one-off integration constraints, one explicit `->` pair per line, with a comment on the offending import explaining why.

## Code organization

- Non-exempt modules are class-managed: at least one top-level class, no top-level business functions. Exempt: `main.py`, `src/routers/**`, `tests/**`.
- Class body order: private variables, public variables, private methods, public methods.
- Naming follows the table in `CLAUDE.md`; `ruff` enforces it.

## Adding a layer-crossing feature

1. Put the data shape in `src/types`.
2. Put the side effect (DB, HTTP call, model call) in the layer that owns it.
3. Wire concrete implementations in `main.py` or in `src/runtime` providers.
4. Run `uv run lint-imports && uv run pytest tests/architecture/`.

## Decisions

Architecture decision records go in `docs/adr/` (context, decision, consequences, date). No ADRs yet.
