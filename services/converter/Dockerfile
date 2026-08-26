FROM msconvert_cli AS msconvert-cli-source
FROM mzmlpy_source AS mzmlpy-source
FROM spxtacular_source AS spxtacular-source

FROM docker:29-cli

RUN apk add --no-cache python3 py3-pip

WORKDIR /app
COPY --from=msconvert-cli-source / /opt/msconvert-cli
COPY --from=mzmlpy-source /pyproject.toml /README.md /LICENSE /opt/mzmlpy/
COPY --from=mzmlpy-source /src /opt/mzmlpy/src
COPY --from=spxtacular-source /pyproject.toml /README.md /LICENSE /opt/spxtacular/
COPY --from=spxtacular-source /src /opt/spxtacular/src
COPY pyproject.toml README.md ./
COPY src ./src
RUN python3 -m pip install --break-system-packages --no-cache-dir \
  /opt/msconvert-cli \
  /opt/mzmlpy \
  '/opt/spxtacular[mzml]' \
  .

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "__import__('urllib.request').request.urlopen('http://api:8000/health', timeout=3)"

ENTRYPOINT ["spectarr-converter-worker"]
