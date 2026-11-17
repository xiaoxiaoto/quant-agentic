# AGENTS.md

Project-wide instructions for coding agents. The root file follows the `AGENTS.md` standard; progressive-disclosure guides under `agents/` use `AGENT.md`.

## Project

quant-agentic - TODO(init-project): one sentence describing what this project does.

## Start here

1. Read `CLAUDE.md` for architecture, commands, and project glossary.
2. Read only the relevant nested guide under `agents/`:
   - `agents/coding/AGENT.md` for implementation and refactoring.
   - `agents/architecture/AGENT.md` for layer-boundary decisions.
   - `agents/testing/AGENT.md` for test and quality-gate selection.
   - `agents/review/AGENT.md` for code review.
   - `agents/docs/AGENT.md` for README, docs, and ADR work.
   - `agents/operations/AGENT.md` for deploy, env, migration, and incident work.
3. Keep task-specific knowledge in the narrowest useful `agents/**/AGENT.md`; keep this root file short.

## Commands

All package and project commands use `uv`.

- `make dev` — start the dev server: `uv run uvicorn main:app --reload --port 8000`.
- `uv run python main.py` — run the composition root directly (port 8000, reload enabled).
- `make check-all` — lint, format, typecheck, architecture gates, and tests in one shot.

## Safety rails

- Code comments and docstrings must be written in Chinese, and must not contain planning document clause numbers such as (§1.0) or G-1.
- Configuration files (`.env` and `.env.example`) must be organized into logical, functional blocks separated by clear section headers.
- Make assumptions explicit before editing.
- Keep changes surgical and tied to the request.
- Preserve existing project-specific guidance when merging this file.
- Do not confuse root `agents/` instruction files with runtime `src/agents/` application code.
