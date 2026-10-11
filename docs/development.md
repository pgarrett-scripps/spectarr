# Development

For a normal installation, use the [published container](../release/README.md). The root `compose.yaml` is for building from source.

## Build from source

Source builds require sibling `msconvert-cli`, `mzmlpy`, and `spxtacular` repositories at the revisions listed in `DEPENDENCIES.env`. CI also applies `release/dependency-patches/msconvert-cli-sage.patch` to the pinned converter checkout. Inspect existing changes before applying it to a working checkout.

```text
Repos/
├── spectarr/
├── msconvert-cli/
├── mzmlpy/
└── spxtacular/
```

With those dependencies prepared:

```bash
make up
```

Open [http://localhost:3280](http://localhost:3280). Configuration is optional. Start from `.env.example` when you need different ports or paths. Keep generated secrets blank to use persistent automatic secrets.

## Checks

Install the locked backend development environment and dashboard dependencies:

```bash
uv sync --project backend --locked --extra dev --extra sdrf
npm ci --prefix frontend
make test
```

`make test` includes component tests, type checks, linting, the dashboard build, version checks, and production dependency consistency. Services use the sibling source checkouts.

Validate release Compose configuration without creating an environment file:

```bash
make release-check
```

Run the container integration workflow on a Docker-capable host:

```bash
SPECTARR_RUN_E2E=true scripts/ci-integration.sh
```

This creates a separate test installation. The [release checklist](release-checklist.md) covers packaging, vendor acceptance, backup restoration, and promotion of a tested image.

Set `SPECTARR_MSCONVERT_RS_TEST_BINARY` to an absolute path for a compatible Linux native executable to include native conversion in the container rehearsal. The optional override mounts the executable read-only. The smoke test checks source identity, 64-bit recipe defaults, native provenance, and the final downloadable artifact checksum. The same variable enables the converter service's five native binary integration tests.

After dependency updates, run `uv lock --project backend` and `python3 scripts/check_dependencies.py --write`, then repeat the checks above. `DEPENDENCIES.env` pins the exact reader source releases. Supply matching checkout paths through `MSCONVERT_CLI_SOURCE`, `MZMLPY_SOURCE`, and `SPXTACULAR_SOURCE` for `make test`, or the corresponding `SPECTARR_*_SOURCE` variables for Docker builds.

## Endpoints

| Surface | Default URL |
| --- | --- |
| Dashboard | http://localhost:3280 |
| REST API | http://localhost:3280/api/v1 |
| API explorer | http://localhost:3280/docs |
| MCP | http://localhost:8281/mcp |

MCP has its own loopback bind setting, `SPECTARR_MCP_BIND_ADDRESS`. Changing dashboard access does not need to expose MCP.
