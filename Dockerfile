# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml requirements.txt README.md LICENSE ./
COPY confluence_markdown_mcp ./confluence_markdown_mcp

RUN pip install .

# Run as a non-root user.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown -R app:app /data
USER app
WORKDIR /data

EXPOSE 8000

# Default to the plain HTTP API bound to all interfaces so the container is
# reachable from the host. MCP itself is stdio-only.
ENTRYPOINT ["confluence-markdown-mcp"]
CMD ["serve-http", "--host", "0.0.0.0", "--port", "8000"]
