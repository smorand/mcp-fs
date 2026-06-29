# Makefile Documentation

## Overview

`make` is the single interface for every developer operation on mcp-fs. The
Makefile follows the `python` skill template (uv based dependency management and
Docker support); it auto-detects the project name from `pyproject.toml` and the
importable package directory under `src/`. Two project-specific, non-Python
targets are added: `serve` and `test-integration`.

## Key Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PROJECT_NAME` | Auto-detected from `pyproject.toml` | `mcp-fs` |
| `PACKAGE` | Importable package (`PROJECT_NAME` with dashes mapped to underscores) | `mcp_fs` |
| `ENTRY_POINT` | First `.py` in `src/` (not `__init__.py`) | `app` |
| `SRC_DIR` | Source directory | `src` |
| `VERSION` | Derived from the git tag (`git describe`), injected at build time | `dev` |
| `MAKE_DOCKER_PREFIX` | Docker registry prefix | empty |
| `DOCKER_TAG` | Docker image tag | `latest` |

## Dependency Management

| Target | Description |
|--------|-------------|
| `sync` | Install/update dependencies with `uv sync` |

## Run Targets

```bash
make run                    # Run the CLI via uv
make run ARGS='--help'      # Run with arguments
make run-dev                # Run the entry point directly
make serve                  # Start the MCP server with config/local.yaml
```

## Test Targets

| Target | Description |
|--------|-------------|
| `test` | Run tests with pytest |
| `test-cov` | Run tests with coverage report (>= 80% enforced) |
| `test-integration` | Run live integration tests (`MCP_FS_INTEGRATION=1`, needs MinIO/S3) |

## Code Quality Targets

| Target | Description |
|--------|-------------|
| `lint` | Check code style with Ruff |
| `lint-fix` | Auto-fix lint issues with Ruff |
| `format` | Format code with Ruff |
| `format-check` | Check formatting without changes |
| `typecheck` | Run mypy type checking (strict) |
| `security` | Run bandit security scanner |
| `check` | Full quality gate: lint, format-check, typecheck, security, test-cov |

## Build & Install Targets

| Target | Description |
|--------|-------------|
| `build` | Write the version into `src/mcp_fs/version.py`, then build wheel and sdist |
| `install` | Install as a uv tool (system-wide) |
| `uninstall` | Remove the uv tool |

## Docker Targets

| Target | Description |
|--------|-------------|
| `docker-build` | Build the Docker image (passes `APP_VERSION=$(VERSION)`) |
| `docker-push` | Push the Docker image to the registry |
| `docker` | Build and push the Docker image |
| `run-up` | Build the image and start docker compose |
| `run-down` | Stop docker compose services |

Example with a custom registry:
```bash
MAKE_DOCKER_PREFIX=gcr.io/my-project/ DOCKER_TAG=v1.0.0 make docker
```

## Cleanup Targets

| Target | Description |
|--------|-------------|
| `clean` | Remove caches and build artifacts |
| `clean-all` | Remove everything including venv and lock file |

## Other Targets

| Target | Description |
|--------|-------------|
| `info` | Show project information |
| `help` | Show the help message |
