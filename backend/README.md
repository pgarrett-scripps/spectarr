# Spectarr backend

Spectarr is a self-hosted library and processing orchestrator for mass spectrometry data. This service owns metadata, immutable artifact ingestion, conversion recipes, jobs, and the versioned REST API.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
uvicorn spectarr.main:app --reload
```

The default database is SQLite at `./data/spectarr.db`. Artifacts are stored at `./data/storage`. Immutable objects live below `.spectarr`, while normal filenames are exposed below `library`. Set `SPECTARR_DATABASE_URL` to a SQLAlchemy PostgreSQL URL for PostgreSQL.

Alembic migrations run during application startup. Pre-Alembic MVP databases are detected and adopted without replacing their existing tables. Tests may still create isolated schemas directly.

## Authentication

Authentication is enabled by default. On a fresh instance, call `GET /api/v1/auth/bootstrap/status`, then create the first administrator with `POST /api/v1/auth/bootstrap`. Browser logins use expiring bearer sessions that are revoked by `POST /api/v1/auth/logout`. Manually created API tokens are separate credentials and retain only their explicitly selected scopes. Set `SPECTARR_SESSION_HOURS` to change the default 24-hour browser session lifetime.

Set `SPECTARR_AUTH_MODE=local` for an intentional no-login installation. Requests without credentials become the local administrator from `SPECTARR_LOCAL_USER`, while bearer and worker credentials keep their normal identities. An automatically created local account has no usable password. `GET /api/v1/auth/config` lets the dashboard discover the mode. The default loopback `SPECTARR_BIND_ADDRESS` prevents accidental network exposure. A non-loopback address requires `SPECTARR_ALLOW_REMOTE_NO_AUTH=true`.

Production deployments should set:

- `SPECTARR_SECRET_KEY` to a stable random secret used to derive webhook signing keys
- `SPECTARR_WORKER_TOKEN` while legacy local workers still use the compatibility header
- `SPECTARR_DATABASE_URL` to the PostgreSQL connection URL
- `SPECTARR_STORAGE_ROOT` to the shared durable storage mount
- `SPECTARR_LIBRARY_ROOT` to the human-readable library path, which defaults to `<storage root>/library`
- `SPECTARR_LIBRARY_LINK_MODE` to `auto`, `hardlink`, or `copy`
- `SPECTARR_LIBRARY_PROJECT_TEMPLATE` for project directory names
- `SPECTARR_LIBRARY_FILENAME_TEMPLATE` for names inside flat format directories
- `SPECTARR_IMPORT_ROOTS` to the explicitly allowed path-import roots

The older `SPECTARR_AUTH_ENABLED=false` setting maps to local mode for compatibility.

Useful endpoints:

- `GET /health`
- `GET /api/v1/auth/config`
- `GET /api/v1/system/health`
- `GET /docs`
- `GET /api/v1/projects`
- `POST /api/v1/runs/{run_id}/artifacts/upload`
- `POST /api/v1/runs/{run_id}/artifacts/import`
- `GET /api/v1/library`
- `POST /api/v1/library/rebuild`
- `GET /api/v1/runs/{run_id}/manifest`
- `GET /api/v1/projects/{project_id}/manifest`
- `GET /api/v1/projects/{project_id}/library`
- `GET /api/v1/recipes`
- `PATCH /api/v1/recipes/{recipe_id}`
- `POST /api/v1/processing-batches/preview`
- `POST /api/v1/processing-batches`
- `GET /api/v1/processing-batches/{batch_id}`
- `POST /api/v1/processing-batches/{batch_id}/retry`
- `POST /api/v1/processing-batches/{batch_id}/cancel`

Path imports are disabled unless `SPECTARR_IMPORT_ROOTS` contains one or more allowed roots. Uploads remain available without path imports.

Processing profiles are named and revisioned conversion configurations. Fresh instances receive Standard mzML and Standard MGF. A profile may use typed conversion parameters or one of MSCLI's packaged named configs. Project, experiment, and selected-run batches expand into one durable job per source and profile. Every job snapshots the exact profile revision before it enters the worker queue.

Projects also own revisioned SDRF v1.1.0 metadata. The API can generate a document from runs, import and export exact TSV, preserve duplicate legal headers, synchronize multiplexed sample mappings, validate against official templates and ontologies, and build a repository package with data files and checksums. The production image installs `sdrf-pipelines[ontology]`. Structural validation remains available if a development environment omits that optional dependency.

Key routes are `GET` and `PUT /api/v1/projects/{project_id}/sdrf`, `POST /sdrf/import`, `POST /sdrf/generate`, `POST /sdrf/validate`, `GET /sdrf/export`, `GET /submission/preview`, and `GET /submission/export`.
