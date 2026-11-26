.PHONY: dev backtest paper live train qlib-status qlib-export qlib-train web-install web-dev web-build web-test lint format typecheck arch-check test check-all

dev:
	uv run uvicorn main:app --reload --port 8000

backtest:
	uv run python main.py backtest

paper:
	uv run python main.py paper run

live:
	uv run python main.py live run

train:
	uv run python -m src.runtime.train

qlib-status:
	uv run python -m src.runtime.qlib_research status

qlib-export:
	uv run python -m src.runtime.qlib_research export

qlib-train:
	uv run python -m src.runtime.qlib_research train

web-install:
	pnpm --dir web install

web-dev:
	pnpm --dir web dev

web-build:
	pnpm --dir web build

web-test:
	pnpm --dir web test

lint:
	uv run ruff check

format:
	uv run ruff format

typecheck:
	uv run pyright

arch-check:
	uv run lint-imports
	uv run pytest tests/architecture/

test:
	uv run pytest

check-all: lint format typecheck arch-check test
