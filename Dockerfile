# =============================================================================
# Multi-stage Python Dockerfile for mcp-fs
# =============================================================================

# =============================================================================
# Stage 1: Build dependencies and install package
# =============================================================================
FROM python:3.13-slim AS builder

WORKDIR /app

# Copy uv from official image (faster and smaller than pip install)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Inject the build version into the package before installing
ARG APP_VERSION=dev
RUN echo "\"\"\"Application version, overridden at build time from the git tag.\"\"\"" > src/mcp_fs/version.py && \
    echo "" >> src/mcp_fs/version.py && \
    echo "__version__: str = \"${APP_VERSION}\"" >> src/mcp_fs/version.py

# Install package and dependencies (without dev packages)
RUN uv sync --frozen --no-dev --no-editable

# =============================================================================
# Stage 2: Runtime image
# =============================================================================
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Create non-root user for security (UID 10001)
RUN groupadd --gid 10001 appgroup && \
    useradd \
        --uid 10001 \
        --gid appgroup \
        --shell /bin/false \
        --no-create-home \
        appuser

# Copy virtual environment from builder (includes installed package)
COPY --from=builder /app/.venv /app/.venv

# Copy the default configuration
COPY config/ ./config/

# Add venv to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Switch to non-root user
USER appuser:appgroup

EXPOSE 5002

# MCP streamable-HTTP server; override the config via CMD or a mounted file.
ENTRYPOINT ["mcp-fs"]
CMD ["serve", "--config", "config/local.yaml"]
