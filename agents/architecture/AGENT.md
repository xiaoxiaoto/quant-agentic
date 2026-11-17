# Architecture Agent Guide

Use this guide when deciding where code belongs or handling dependency-direction violations.

## Layer order

```text
types -> config -> repositories -> services -> agents -> runtime -> routers
```

Each layer may import layers to its left. Reverse imports are forbidden.

## Decisions

- Put pure domain data in `src/types`.
- Put settings, constants, and logging setup in `src/config`.
- Put database access and ORM models in `src/repositories`.
- Put business rules in `src/services`.
- Put LLM orchestration and tool wrappers in runtime `src/agents`.
- Put dependency injection, middleware, lifespan, scheduler, and request context in `src/runtime`.
- Put HTTP and WebSocket entry points in `src/routers`.

When a dependency points upward, invert it with parameters, define a lower-layer interface, or move the shared code down. Do not relax the architecture contract to make tests pass.

