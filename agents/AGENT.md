# Agent Instruction Index

This directory stores progressive-disclosure guidance for coding agents. Load the smallest guide that matches the task.

## Guides

- `coding/AGENT.md` - implementation, refactors, and bug fixes.
- `architecture/AGENT.md` - dependency direction, layer placement, and boundary violations.
- `testing/AGENT.md` - test scope, quality gates, and verification.
- `review/AGENT.md` - code-review priorities and report format.
- `docs/AGENT.md` - README, docs, and ADR guidance.
- `operations/AGENT.md` - deploy, env, migration, and incident guidance.

## Rules

- Keep reusable instructions here; keep runtime code under `src/`.
- Add a nested `AGENT.md` only when it saves future agents from re-discovering local conventions.
- Prefer short checklists and concrete commands over broad advice.
