# syntax=docker/dockerfile:1.7

ARG SPECTARR_NODE_IMAGE=node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32
ARG SPECTARR_DOCKER_CLI_IMAGE=docker:27-cli@sha256:851f91d241214e7c6db86513b270d58776379aacc5eb9c4a87e5b47115e3065c
ARG SPECTARR_DOTNET_RUNTIME_IMAGE=mcr.microsoft.com/dotnet/runtime:8.0-bookworm-slim@sha256:9d94ecf60a21c6e7a784cf0761fbd4a8391646617a0ff2f39621443d580cc2c3
ARG SPECTARR_PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

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
      ca-certificates libgcc-s1 libicu72 libssl3 libstdc++6 tzdata zlib1g \
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
RUN groupadd --system --gid 1000 spectarr \
    && useradd --system --uid 1000 --gid spectarr --home-dir /app spectarr \
    && mkdir -p /data/storage /data/scratch /imports \
    && chown -R spectarr:spectarr /app /data

COPY --from=dashboard-build --chown=spectarr:spectarr /build/frontend/dist /app/dashboard

EXPOSE 8000 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "__import__('urllib.request').request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["spectarr-server"]
