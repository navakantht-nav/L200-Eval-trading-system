# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY trading_agent ./trading_agent
COPY mcp_toolkit ./mcp_toolkit
COPY app ./app
COPY agent.py services.py ./

RUN uv sync --no-dev

FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --gid 10001 agentuser && \
    useradd --uid 10001 --gid agentuser --shell /bin/bash --create-home agentuser

WORKDIR /app

COPY --from=builder --chown=agentuser:agentuser /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    OTEL_SERVICE_NAME="alphapulse-trading-agent"

USER agentuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)" || exit 1

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8080"]
