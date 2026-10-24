# Glama can wrap this stdio MCP process in its hosted HTTP endpoint.
FROM ghcr.io/astral-sh/uv:0.8.17 AS uv
FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/src/migration_mcp/.venv \
    MIGRATION_MCP_NODE=/usr/local/bin/node \
    MIGRATION_MCP_GRAPHIFY=/opt/graphify/bin/graphify \
    MIGRATION_MCP_WORKSPACE_ROOT=/data/workspaces \
    PATH=/opt/graphify/bin:/app/src/migration_mcp/.venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/graphify \
    && /opt/graphify/bin/pip install --no-cache-dir graphifyy==0.9.94 \
    && useradd --create-home --uid 10001 migration \
    && mkdir -p /app /data/workspaces \
    && chown -R migration:migration /app /data

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app
COPY package.json graphify-runner.js ./
COPY src/graphify ./src/graphify
COPY src/migration_mcp ./src/migration_mcp
RUN uv sync --locked --project /app/src/migration_mcp --no-dev \
    && chown -R migration:migration /app

USER migration
VOLUME ["/data"]
EXPOSE 8000
CMD ["/app/src/migration_mcp/.venv/bin/python", "/app/src/migration_mcp/server.py"]
