.PHONY: dev backtest paper live train qlib-status qlib-export qlib-train qlib-au9999-export qlib-au9999-train qlib-au9999-backtest qlib-au9999-signal timesfm-status timesfm-prepare timesfm-train timesfm-backtest timesfm-pipeline web-install web-dev web-build web-test lint format typecheck arch-check test check-all

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

qlib-au9999-export:
	uv run python -m src.runtime.qlib_research export --source kline --instruments SGE-Au99.99 --freq day --kline-interval day --kline-source sge_daily --include-macro --output-dir data/qlib_au9999_day

qlib-au9999-train:
	uv run python -m src.runtime.qlib_research train --experiment-name au9999_timing --provider-uri data/qlib_au9999_day --instruments SGE-Au99.99 --freq day --model lgb --preset au9999_timing --horizons 1d 3d --window-mode rolling --train-window-bars 750 --step-bars 63 --robust-zscore

qlib-au9999-backtest:
	uv run python -m src.runtime.qlib_research backtest --provider-uri data/qlib_au9999_day --instrument SGE-Au99.99 --preset au9999_timing --window-mode rolling --train-window-bars 750 --step-bars 63

qlib-au9999-signal:
	uv run python -m src.runtime.qlib_research signal --provider-uri data/qlib_au9999_day --instrument SGE-Au99.99

timesfm-status:
	uv run python -m src.runtime.timesfm_pipeline status

timesfm-prepare:
	uv run python -m src.runtime.timesfm_pipeline prepare

timesfm-train:
	uv run python -m src.runtime.timesfm_pipeline train

timesfm-backtest:
	uv run python -m src.runtime.timesfm_pipeline backtest

timesfm-pipeline:
	uv run python -m src.runtime.timesfm_pipeline run-all

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
