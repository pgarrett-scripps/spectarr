FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir '.[postgres,sdrf]'

RUN groupadd --system spectarr && useradd --system --gid spectarr --home-dir /app spectarr \
    && mkdir -p /data/storage \
    && chown -R spectarr:spectarr /app /data

USER spectarr

ENV SPECTARR_DATABASE_URL=sqlite:////data/spectarr.db \
    SPECTARR_STORAGE_ROOT=/data/storage

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "__import__('urllib.request').request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "spectarr.main:app", "--host", "0.0.0.0", "--port", "8000"]
