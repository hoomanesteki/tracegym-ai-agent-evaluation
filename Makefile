# TraceGym developer commands. Everything runs through `uv` so there is no
# "works on my machine": the interpreter and deps are pinned in uv.lock.
.DEFAULT_GOAL := help
.PHONY: help setup test lint fmt demo report gate calibrate probe demodata clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv and install dev + all extras
	uv sync --extra dev --extra all

test: ## Run the test suite
	uv run pytest

lint: ## Lint with ruff
	uv run ruff check .

fmt: ## Format with ruff
	uv run ruff format .

demo: ## Run the zero-key demo end to end
	uv run tg demo

report: ## Render the HTML report for the last run
	uv run tg report --open

gate: ## Run the regression gate against the promoted baseline
	uv run tg gate --vs baseline

calibrate: ## Compute judge-vs-human agreement from labels
	uv run tg calibrate

probe: ## Measure live provider rate limits into limits.yaml
	uv run python scripts/probe_limits.py

demodata: ## Rebuild the bundled zero-key demo assets
	uv run python scripts/build_demodata.py

clean: ## Remove caches and build artifacts
	rm -rf .cache runs dist build .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
