FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

ENV SPECTARR_MCP_TRANSPORT=http

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "__import__('socket').create_connection(('127.0.0.1', 8001), timeout=3).close()"

ENTRYPOINT ["spectarr-mcp"]
