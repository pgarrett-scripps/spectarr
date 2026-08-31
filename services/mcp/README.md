# Spectarr MCP server

This package exposes Spectarr library resources and tools over MCP standard input and output or stateless Streamable HTTP. It calls only the public REST API.

Read operations are available by default. They cover runs, artifacts, latest metadata extractions, QC summaries, instrument agent status, automation rules, named processing profiles, processing batches, audit status, and event outbox status.

Conversion, scoped batch processing, extraction, annotation, retry, and automation toggle tools require all of the following:

- `SPECTARR_MCP_ALLOW_WRITES=true`
- An API key with the relevant server-side scope
- `confirm=true` in the individual tool call

Configuration:

```text
SPECTARR_API_URL=http://127.0.0.1:8000
SPECTARR_API_KEY=<scoped service token>
SPECTARR_API_TIMEOUT=30
SPECTARR_MCP_ALLOW_WRITES=false
SPECTARR_MCP_TRANSPORT=stdio
```

Run locally:

```bash
python -m pip install -e services/mcp
spectarr-mcp
```

For the container endpoint at `http://localhost:8001/mcp`, set `SPECTARR_MCP_TRANSPORT=http` and expose port 8001.

The server has no direct database or filesystem access. This keeps it compatible with future Spectarr API versions and separate search applications.

SDRF resources expose each project document and repository submission readiness. Read tools return the exact ordered table and mapping state. Write mode adds confirmed tools for regenerating SDRF from current project data and validating it with optional ontology checks.

Audit status and some agent administration views require an API token with admin scope. Other read and write operations remain subject to the backend scopes associated with `SPECTARR_API_KEY`.

For a single-host development deployment, `SPECTARR_WORKER_TOKEN` can be used instead. A scoped bearer token is preferred for networked deployments. If both are set, the bearer token takes precedence.
