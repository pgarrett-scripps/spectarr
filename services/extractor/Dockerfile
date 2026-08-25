FROM python:3.12-slim

ARG SPECTARR_INSTALL_OPENMASSSPEC=true

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .
RUN test "$SPECTARR_INSTALL_OPENMASSSPEC" != "true" || python -m pip install --no-cache-dir '.[openmassspec]'

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "__import__('urllib.request').request.urlopen('http://api:8000/health', timeout=3)"

ENTRYPOINT ["spectarr-extractor-worker"]
