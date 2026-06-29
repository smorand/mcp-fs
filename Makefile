# mcp-fs - developer tasks
.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help sync run format lint typecheck security test test-integration check clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

sync: ## Install dependencies (uv)
	uv sync

run: ## Start the MCP server with config/local.yaml
	uv run mcp-fs serve --config config/local.yaml

format: ## Format the code (ruff)
	uv run ruff format src tests

lint: ## Lint and auto-fix (ruff)
	uv run ruff check --fix src tests

typecheck: ## Strict type-check (mypy)
	uv run mypy src

security: ## Security scan (bandit)
	uv run bandit -q -c pyproject.toml -r src

test: ## Run unit + functional tests with coverage (no live stack)
	uv run pytest --cov=mcp_fs --cov-report=term-missing -m "not integration"

test-integration: ## Run the live integration test (needs MinIO running)
	MCP_FS_INTEGRATION=1 uv run pytest -q -m integration tests/integration

check: lint format typecheck security test ## Full quality gate
	@echo "All checks passed."

clean: ## Remove caches and runtime state
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
