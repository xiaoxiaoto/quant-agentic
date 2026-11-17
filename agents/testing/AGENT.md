# Testing Agent Guide

Use this guide when choosing verification for a change.

## Quality gates

```bash
uv run ruff check
uv run ruff format
uv run pyright
uv run lint-imports
uv run pytest
uv run pytest tests/architecture/
```

## Test scope

- For bug fixes, add or run a test that reproduces the failure before confirming the fix.
- For validation changes, cover invalid and boundary inputs.
- For refactors, run relevant tests before and after when practical.
- For layer changes, always run `uv run lint-imports` and `uv run pytest tests/architecture/`.

