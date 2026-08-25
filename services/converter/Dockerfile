FROM msconvert_cli AS msconvert-cli-source

FROM docker:27-cli

RUN apk add --no-cache python3 py3-pip

WORKDIR /app
COPY --from=msconvert-cli-source / /opt/msconvert-cli
COPY pyproject.toml README.md ./
COPY src ./src
RUN python3 -m pip install --break-system-packages --no-cache-dir /opt/msconvert-cli .

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "__import__('urllib.request').request.urlopen('http://api:8000/health', timeout=3)"

ENTRYPOINT ["spectarr-converter-worker"]
