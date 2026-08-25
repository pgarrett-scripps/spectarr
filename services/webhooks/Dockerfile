FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir . \
    && useradd --system --uid 10001 --no-create-home webhook-worker

USER webhook-worker

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "__import__('urllib.request').request.urlopen('http://api:8000/health', timeout=3)"

ENTRYPOINT ["spectarr-webhook-worker"]
