.PHONY: dev backtest paper live train web-install web-dev web-build lint format typecheck arch-check test check-all

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

web-install:
	pnpm --dir web install

web-dev:
	pnpm --dir web dev

web-build:
	pnpm --dir web build

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
