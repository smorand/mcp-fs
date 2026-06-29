.PHONY: sync run run-dev serve test test-cov test-integration lint lint-fix format format-check typecheck security check build install uninstall docker-build docker-push docker run-up run-down clean clean-all info help

# Auto-detect project name from pyproject.toml
PROJECT_NAME=$(shell grep -m1 '^name' pyproject.toml 2>/dev/null | sed 's/.*= *"\([^"]*\)".*/\1/')

# Importable package directory under src/ (project name with dashes as underscores)
PACKAGE=$(shell echo $(PROJECT_NAME) | tr '-' '_')

# Auto-detect entry point (first .py file in src/ that's not __init__.py)
HAS_SRC_DIR=$(shell [ -d src ] && echo "yes" || echo "no")
ifeq ($(HAS_SRC_DIR),yes)
	ENTRY_POINT=$(shell ls src/*.py 2>/dev/null | grep -v __init__.py | head -1 | xargs basename 2>/dev/null | sed 's/\.py$$//')
	SRC_DIR=src
else
	ENTRY_POINT=$(shell ls *.py 2>/dev/null | grep -v __init__.py | head -1 | xargs basename 2>/dev/null | sed 's/\.py$$//')
	SRC_DIR=.
endif

# Default entry point if none found
ENTRY_POINT := $(if $(ENTRY_POINT),$(ENTRY_POINT),app)

# Version derived from the git tag (injected into the package at build time)
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")

# Python version check
PYTHON_VERSION=$(shell python3 --version 2>/dev/null | cut -d' ' -f2)

# Detect if uv is available
HAS_UV=$(shell command -v uv >/dev/null 2>&1 && echo "yes" || echo "no")

# Docker configuration
MAKE_DOCKER_PREFIX ?=
DOCKER_TAG ?= latest

# ============================================================================
# DEPENDENCY MANAGEMENT
# ============================================================================

## sync: Install/update project dependencies using uv
sync:
ifeq ($(HAS_UV),yes)
	@echo "Syncing dependencies with uv..."
	@uv sync
	@echo "Dependencies synced!"
else
	@echo "Error: uv not found. Install it from https://docs.astral.sh/uv/"
	@exit 1
endif

# ============================================================================
# RUNNING
# ============================================================================

## run: Run the CLI application via uv
run: sync
ifdef ARGS
	@echo "Running $(PROJECT_NAME) with args: $(ARGS)..."
	@uv run $(PROJECT_NAME) $(ARGS)
else
	@echo "Running $(PROJECT_NAME)..."
	@uv run $(PROJECT_NAME)
endif

## run-dev: Run entry point directly (useful during development)
run-dev:
ifeq ($(HAS_SRC_DIR),yes)
ifdef ARGS
	@echo "Running $(SRC_DIR)/$(ENTRY_POINT).py with args: $(ARGS)..."
	@uv run python $(SRC_DIR)/$(ENTRY_POINT).py $(ARGS)
else
	@echo "Running $(SRC_DIR)/$(ENTRY_POINT).py..."
	@uv run python $(SRC_DIR)/$(ENTRY_POINT).py
endif
else
ifdef ARGS
	@echo "Running $(ENTRY_POINT).py with args: $(ARGS)..."
	@uv run python $(ENTRY_POINT).py $(ARGS)
else
	@echo "Running $(ENTRY_POINT).py..."
	@uv run python $(ENTRY_POINT).py
endif
endif

## serve: Start the MCP server with the local configuration (project-specific)
serve: sync
	@echo "Starting $(PROJECT_NAME) on the local configuration..."
	@uv run $(PROJECT_NAME) serve --config config/local.yaml

# ============================================================================
# TESTING
# ============================================================================

## test: Run tests with pytest (supports ARGS='...' for extra arguments)
test:
	@echo "Running tests..."
ifdef ARGS
	@uv run pytest -v $(ARGS)
else
	@uv run pytest -v
endif
	@echo "Tests complete!"

## test-cov: Run tests with coverage report
test-cov:
	@echo "Running tests with coverage..."
	@uv run pytest -v --cov=$(SRC_DIR) --cov-report=term-missing
	@echo "Tests complete!"

## test-integration: Run the live integration tests (needs a MinIO/S3 stack, project-specific)
test-integration:
	@echo "Running integration tests against the live stack..."
	@MCP_FS_INTEGRATION=1 uv run pytest -q -m integration tests/integration
	@echo "Integration tests complete!"

# ============================================================================
# CODE QUALITY
# ============================================================================

## lint: Check code style with Ruff
lint:
	@echo "Running Ruff linter..."
	@uv run ruff check .
	@echo "Lint check complete!"

## lint-fix: Auto-fix lint issues with Ruff
lint-fix:
	@echo "Running Ruff linter with auto-fix..."
	@uv run ruff check --fix .
	@echo "Lint fix complete!"

## format: Format code with Ruff
format:
	@echo "Formatting code with Ruff..."
	@uv run ruff format .
	@echo "Format complete!"

## format-check: Check code formatting without changes
format-check:
	@echo "Checking code format..."
	@uv run ruff format --check .
	@echo "Format check complete!"

## typecheck: Run type checking with mypy
typecheck:
	@echo "Running mypy type checker..."
	@uv run mypy $(SRC_DIR)/
	@echo "Type check complete!"

## security: Run bandit security scanner
security:
	@echo "Running bandit security scanner..."
	@uv run bandit -r $(SRC_DIR)/ -c pyproject.toml 2>/dev/null || uv run bandit -r $(SRC_DIR)/
	@echo "Security scan complete!"

## check: Run all quality checks (lint, format, typecheck, security, tests+coverage)
check: lint format-check typecheck security test-cov
	@echo "All checks passed!"

# ============================================================================
# BUILD & INSTALL
# ============================================================================

## build: Build wheel and sdist packages
build: sync
	@echo "Writing version $(VERSION) into src/$(PACKAGE)/version.py..."
	@echo '"""Application version, overridden at build time from the git tag."""' > src/$(PACKAGE)/version.py
	@echo '' >> src/$(PACKAGE)/version.py
	@echo '__version__: str = "$(VERSION)"' >> src/$(PACKAGE)/version.py
	@echo "Building package..."
	@uv build
	@echo "Build complete! Artifacts in dist/"

## install: Install as a uv tool (available system-wide)
install:
	@echo "Installing $(PROJECT_NAME) as uv tool..."
	@uv tool install . --reinstall --force
	@echo "Install complete! Run '$(PROJECT_NAME)' from anywhere."

## uninstall: Remove uv tool
uninstall:
	@echo "Uninstalling $(PROJECT_NAME)..."
	@uv tool uninstall $(PROJECT_NAME) 2>/dev/null || echo "Not installed"
	@echo "Uninstall complete!"

# ============================================================================
# CLEANUP
# ============================================================================

## clean: Remove caches and build artifacts
clean:
	@echo "Cleaning up..."
	@rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache
	@rm -rf src/__pycache__ tests/__pycache__
	@rm -rf dist build *.egg-info src/*.egg-info
	@rm -rf .coverage htmlcov
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@echo "Clean complete!"

## clean-all: Remove everything including venv and lock file
clean-all: clean
	@echo "Removing virtual environment and lock file..."
	@rm -rf .venv
	@rm -f uv.lock
	@echo "Full clean complete!"

# ============================================================================
# DOCKER
# ============================================================================

## docker-build: Build Docker image
docker-build:
	@echo "Building Docker image: $(MAKE_DOCKER_PREFIX)$(PROJECT_NAME):$(DOCKER_TAG)..."
	@docker build --build-arg APP_VERSION=$(VERSION) -t $(MAKE_DOCKER_PREFIX)$(PROJECT_NAME):$(DOCKER_TAG) .
	@echo "Docker image built!"

## docker-push: Push Docker image to registry
docker-push:
	@echo "Pushing Docker image: $(MAKE_DOCKER_PREFIX)$(PROJECT_NAME):$(DOCKER_TAG)..."
	@docker push $(MAKE_DOCKER_PREFIX)$(PROJECT_NAME):$(DOCKER_TAG)
	@echo "Docker image pushed!"

## docker: Build and push Docker image
docker: docker-build docker-push

## run-up: Build Docker image and start docker compose
run-up: docker-build
	@echo "Starting services..."
	@PROJECT_NAME=$(PROJECT_NAME) DOCKER_PREFIX=$(MAKE_DOCKER_PREFIX) DOCKER_TAG=$(DOCKER_TAG) docker compose up -d
	@echo "Services started!"

## run-down: Stop docker compose services
run-down:
	@echo "Stopping services..."
	@PROJECT_NAME=$(PROJECT_NAME) DOCKER_PREFIX=$(MAKE_DOCKER_PREFIX) DOCKER_TAG=$(DOCKER_TAG) docker compose down
	@echo "Services stopped!"

# ============================================================================
# INFORMATION
# ============================================================================

## info: Show project information
info:
	@echo "Project Information"
	@echo "==================="
	@echo "Project name:    $(PROJECT_NAME)"
	@echo "Package:         $(PACKAGE)"
	@echo "Entry point:     $(ENTRY_POINT).py"
	@echo "Source dir:      $(SRC_DIR)/"
	@echo "Version:         $(VERSION)"
	@echo "Python version:  $(PYTHON_VERSION)"
	@echo "uv available:    $(HAS_UV)"
	@echo ""
	@echo "Detected structure:"
ifeq ($(HAS_SRC_DIR),yes)
	@echo "  Using src/ layout"
else
	@echo "  Using flat layout"
endif

## help: Show this help message
help:
	@echo "Python Project Makefile"
	@echo "======================="
	@echo ""
	@echo "Dependency Management:"
	@echo "  sync             - Install/update dependencies with uv"
	@echo ""
	@echo "Running:"
	@echo "  run              - Run the CLI application via uv"
	@echo "  run-dev          - Run entry point directly (development)"
	@echo "  serve            - Start the MCP server with config/local.yaml"
	@echo ""
	@echo "Testing:"
	@echo "  test             - Run tests with pytest"
	@echo "  test-cov         - Run tests with coverage report"
	@echo "  test-integration - Run live integration tests (needs MinIO/S3)"
	@echo ""
	@echo "Code Quality:"
	@echo "  lint             - Check code style with Ruff"
	@echo "  lint-fix         - Auto-fix lint issues"
	@echo "  format           - Format code with Ruff"
	@echo "  format-check     - Check formatting without changes"
	@echo "  typecheck        - Run mypy type checking"
	@echo "  security         - Run bandit security scanner"
	@echo "  check            - Run all quality checks (lint, format, typecheck, security, tests+coverage)"
	@echo ""
	@echo "Build & Install:"
	@echo "  build            - Build wheel and sdist packages"
	@echo "  install          - Install as a uv tool (system-wide)"
	@echo "  uninstall        - Remove uv tool"
	@echo ""
	@echo "Docker:"
	@echo "  docker-build     - Build Docker image"
	@echo "  docker-push      - Push Docker image to registry"
	@echo "  docker           - Build and push Docker image"
	@echo "  run-up           - Build Docker image and start docker compose"
	@echo "  run-down         - Stop docker compose services"
	@echo ""
	@echo "Cleanup:"
	@echo "  clean            - Remove caches and build artifacts"
	@echo "  clean-all        - Remove everything including venv"
	@echo ""
	@echo "Information:"
	@echo "  info             - Show project information"
	@echo "  help             - Show this help message"
	@echo ""
	@echo "Examples:"
	@echo "  make serve                  - Start the MCP server locally"
	@echo "  make run ARGS='--help'      - Run with --help flag"
	@echo "  make test ARGS='-k test_foo' - Run specific tests"
	@echo "  make check                  - Run all quality checks before commit"
	@echo "  MAKE_DOCKER_PREFIX=gcr.io/proj/ DOCKER_TAG=v1.0.0 make docker"
	@echo ""
	@echo "Project: $(PROJECT_NAME)"
	@echo "Entry:   $(SRC_DIR)/$(ENTRY_POINT).py"

.DEFAULT_GOAL := help
