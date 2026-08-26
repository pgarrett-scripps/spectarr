FROM mzmlpy_source AS mzmlpy-source
FROM spxtacular_source AS spxtacular-source

FROM python:3.12-slim-bookworm

ARG SPECTARR_INSTALL_OPENMASSSPEC=true

ENV DOTNET_ROOT=/usr/share/dotnet \
  PYTHONNET_RUNTIME=coreclr

RUN apt-get update \
  && apt-get install --yes --no-install-recommends ca-certificates curl \
  && curl --fail --silent --show-error --location \
    https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    --output /tmp/packages-microsoft-prod.deb \
  && dpkg --install /tmp/packages-microsoft-prod.deb \
  && apt-get update \
  && apt-get install --yes --no-install-recommends dotnet-runtime-8.0 \
  && rm -f /tmp/packages-microsoft-prod.deb \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=mzmlpy-source /pyproject.toml /README.md /LICENSE /opt/mzmlpy/
COPY --from=mzmlpy-source /src /opt/mzmlpy/src
COPY --from=spxtacular-source /pyproject.toml /README.md /LICENSE /opt/spxtacular/
COPY --from=spxtacular-source /src /opt/spxtacular/src
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir \
  /opt/mzmlpy \
  '/opt/spxtacular[readers]' \
  .
RUN test "$SPECTARR_INSTALL_OPENMASSSPEC" != "true" || python -m pip install --no-cache-dir '.[openmassspec]'

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "__import__('urllib.request').request.urlopen('http://api:8000/health', timeout=3)"

ENTRYPOINT ["spectarr-extractor-worker"]
