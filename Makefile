.PHONY: dev lint format typecheck arch-check test check-all

dev:
	uv run uvicorn main:app --reload --port 8000

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
