# Operations Agent Guide

Use this guide for deploy, environment, migration, background job, and incident work.

## Before changing operations behavior

- Identify affected entry points, external systems, env vars, queues, schedules, and database migrations.
- Prefer reversible, observable changes.
- Do not invent secret names or production commands; read existing docs and config first.
- Call out manual steps, ordering constraints, and rollback expectations.

## Verification

- Run the narrowest relevant local check first.
- For database changes, verify migration order and downgrade or rollback story when the project supports it.
- For scheduler or worker changes, verify startup path and failure behavior.
- Update docs when an operator needs a new command, env var, or runbook step.

