# syntax=docker/dockerfile:1.7

ARG SPECTARR_NODE_IMAGE=node:26-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80
ARG SPECTARR_DOCKER_CLI_IMAGE=docker:29-cli@sha256:018edbc908e08fcc9dbf029c812c34251e9b4719e6f71ca0e5eae2a987d014ca
ARG SPECTARR_DOTNET_RUNTIME_IMAGE=mcr.microsoft.com/dotnet/runtime:10.0@sha256:ff17a18b639a0327e52c7c296fa2e1abe6e03eb61d8121a8ef67cc6aa430a27e
ARG SPECTARR_PYTHON_IMAGE=python:3.14-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

FROM ${SPECTARR_NODE_IMAGE} AS dashboard-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM ${SPECTARR_DOCKER_CLI_IMAGE} AS docker-cli
FROM ${SPECTARR_DOTNET_RUNTIME_IMAGE} AS dotnet-runtime
FROM msconvert_cli AS msconvert-cli-source
FROM mzmlpy_source AS mzmlpy-source
FROM spxtacular_source AS spxtacular-source

FROM ${SPECTARR_PYTHON_IMAGE}

ARG SPECTARR_INSTALL_OPENMASSSPEC=true

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOTNET_ROOT=/usr/share/dotnet \
    PYTHONNET_RUNTIME=coreclr \
    SPECTARR_DATABASE_URL=sqlite:////data/spectarr.db \
    SPECTARR_STORAGE_ROOT=/data/storage \
    SPECTARR_LIBRARY_ROOT=/data/storage/library \
    SPECTARR_MIGRATION_ROOT=/app/backend \
    SPECTARR_DASHBOARD_ROOT=/app/dashboard \
    SPECTARR_API_URL=http://127.0.0.1:8000 \
    SPECTARR_URL=http://127.0.0.1:8000 \
    SPECTARR_SPECTRUM_READER_URL=http://127.0.0.1:8002 \
    SPECTARR_LOCAL_STORAGE_ROOT=/data/storage \
    SPECTARR_SOURCE_ROOTS=/data/storage \
    SPECTARR_SCRATCH_ROOT=/data/scratch \
    SPECTARR_CONTAINER_DATA_ROOT=/data \
    SPECTARR_ENVIRONMENT=production \
    SPECTARR_CORS_ORIGINS=[] \
    SPECTARR_MCP_TRANSPORT=http \
    SPECTARR_MCP_HOST=0.0.0.0 \
    SPECTARR_MCP_PORT=8001

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
      ca-certificates libgcc-s1 libicu76 libssl3t64 libstdc++6 tzdata zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dotnet-runtime /usr/share/dotnet /usr/share/dotnet
COPY --from=msconvert-cli-source / /opt/msconvert-cli
COPY --from=mzmlpy-source /pyproject.toml /README.md /LICENSE /opt/mzmlpy/
COPY --from=mzmlpy-source /src /opt/mzmlpy/src
COPY --from=spxtacular-source /pyproject.toml /README.md /LICENSE /opt/spxtacular/
COPY --from=spxtacular-source /src /opt/spxtacular/src

WORKDIR /app
COPY backend /app/backend
COPY services/converter /app/services/converter
COPY services/extractor /app/services/extractor
COPY services/mcp /app/services/mcp
COPY services/webhooks /app/services/webhooks
COPY constraints.txt /app/constraints.txt

RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1
RUN python -m pip install --no-cache-dir --constraint /app/constraints.txt \
      /opt/msconvert-cli \
      /opt/mzmlpy \
      '/opt/spxtacular[readers]' \
      '/app/backend[sdrf]' \
      /app/services/converter \
      /app/services/extractor \
      /app/services/mcp \
      /app/services/webhooks
RUN test "$SPECTARR_INSTALL_OPENMASSSPEC" != "true" \
    || python -m pip install --no-cache-dir --constraint /app/constraints.txt '/app/services/extractor[openmassspec]'
RUN python -m pip check \
    && python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python*/ensurepip
RUN groupadd --system --gid 1000 spectarr \
    && useradd --system --uid 1000 --gid spectarr --home-dir /app spectarr \
    && mkdir -p /data/storage /data/scratch /imports \
    && chown -R spectarr:spectarr /app /data

COPY --from=dashboard-build --chown=spectarr:spectarr /build/frontend/dist /app/dashboard

EXPOSE 8000 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "__import__('urllib.request').request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["spectarr-server"]
