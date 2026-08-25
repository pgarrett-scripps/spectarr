# Spectarr

Spectarr is a self-hosted ARR application for mass spectrometry data. It stores immutable source acquisitions, generates reproducible derivatives, extracts scientific metadata and QC summaries, accepts automatic instrument uploads, and exposes a dashboard, REST API, and MCP server.

## Architecture

```text
Instrument folders -> Acquisition agent -> Resumable upload API
                                             |
Dashboard + REST + MCP -> Spectarr API -> PostgreSQL + artifact storage
                                             |
                         +-------------------+------------------+
                         |                                      |
                  Metadata extractor                    MSConvert worker
                         |                                      |
                  QC and TIC/BPC                         mzML, mzXML,
                  summaries                             MGF, and MS2
```

Source files and vendor directory bundles are content-addressed and immutable. Spectarr also maintains an ordinary, human-readable filesystem library with the original filenames. Every derivative and extraction result records its source checksum, recipe, provider, version, and schema version.

## Human-readable filesystem library

External software can mount `data/storage/library` read-only and use files without the Spectarr dashboard or API. The default layout is:

```text
library/
├── spectarr-library.json
└── project-name__projectid/
    ├── spectarr-project.json
    ├── raw/
    │   ├── run-name__sample-name__runid.raw
    │   └── run-name__sample-name__runid.d/
    ├── mzml/
    │   └── run-name__sample-name__runid.mzML
    ├── mzxml/
    ├── mgf/
    ├── ms2/
    └── .spectarr/
        └── runs/
            └── run-uuid.json
```

Each project provides flat, format-specific directories that can be passed directly to a search engine. Experiments and samples remain in PostgreSQL and JSON manifests instead of adding filesystem depth. Short stable IDs prevent collisions while names keep the files understandable. Vendor acquisitions such as Bruker `.d` folders retain their original directory structure.

The default filename template is `{run_name}__{sample_name}__{run_id:8}{extension}`. `SPECTARR_LIBRARY_PROJECT_TEMPLATE` and `SPECTARR_LIBRARY_FILENAME_TEMPLATE` support these tokens: `project_name`, `project_id`, `experiment_name`, `experiment_id`, `sample_name`, `sample_id`, `run_name`, `run_id`, `artifact_id`, `instrument_name`, `acquired_date`, `original_filename`, `original_stem`, `extension`, `format`, `role`, and `recipe_name`. A numeric suffix such as `:8` truncates a token to that length.

On one filesystem, readable files are hard links to immutable objects and consume no extra space. Set `SPECTARR_LIBRARY_LINK_MODE=copy` when independent physical copies are required. `POST /api/v1/library/rebuild` reconstructs the complete readable view and its manifests.

## Start the local stack

Requirements:

- Docker with Compose
- The sibling `msconvert-cli` checkout at `../msconvert-cli`
- Node.js 22 and Python 3.12 only when developing outside containers

Create local configuration:

```bash
cp .env.example .env
openssl rand -hex 32
```

Put the generated value in `SPECTARR_SECRET_KEY`. Replace the worker token and database password as well. Then start Spectarr:

```bash
make up
```

Open `http://localhost:3280`. A new instance asks you to create the first administrator account. Existing pre-Alembic MVP databases are upgraded in place while preserving library data.

Local endpoints:

- Dashboard: `http://localhost:3280`
- REST API: `http://localhost:8280`
- OpenAPI: `http://localhost:8280/docs`
- MCP: `http://localhost:8281/mcp`

## Release distribution

Tagged releases publish versioned API, dashboard, converter, extractor, webhook, and MCP images to GHCR. A GitHub release bundle contains the production Compose file, configuration template, acquisition-agent wheel, and SHA-256 checksum. Image builds publish SBOM and provenance metadata. See [release installation](release/README.md).

Validate production configuration before deployment:

```bash
cp release/.env.example release/.env
# Edit release/.env with generated secrets and the absolute data path.
make release-check
```

Authentication is enabled by default. The local Compose MCP uses the worker credential for immediate read-only access. For networked MCP or Searcharr deployments, create a scoped API token in Settings, set `SPECTARR_API_KEY` in `.env`, then recreate the MCP container. The converter and extractor use the dedicated worker credential.

For a trusted single-user installation, set `SPECTARR_AUTH_MODE=local`. The dashboard skips login and uses an automatically created administrator named by `SPECTARR_LOCAL_USER`. API tokens, instrument agents, and workers continue to authenticate normally. Compose binds all published ports to `127.0.0.1` by default. To expose no-login mode remotely, both `SPECTARR_BIND_ADDRESS=0.0.0.0` and `SPECTARR_ALLOW_REMOTE_NO_AUTH=true` are required. Remote no-login mode gives every network client full administrator access.

Production mode refuses weak application or worker secrets, SQLite, the default PostgreSQL password, and wildcard CORS. Password login has persistent account throttling. Users can rotate their password from Settings, which revokes their other browser sessions. Browser session credentials use session storage and migrate away from persistent local storage. API responses include clickjacking, content-sniffing, referrer, permissions, and authentication-cache protections. Published ports remain loopback-only by default. The converter still needs a Docker-compatible execution service, so a rootless Docker daemon is strongly recommended for production.

Instrument agents default to a backend-managed inbox. Spectarr creates one intake experiment per agent and marks its automatic uploads as needing assignment. The Inbox dashboard supports single and bulk moves into scientific experiments. Reassignment preserves canonical data, metadata, processing history, and audit records while rebuilding the human-readable library links under the destination project. An agent can instead target an existing experiment directly.

## Automatic metadata and QC

Every ready source artifact automatically queues a versioned metadata extraction job. Built-in bounded-memory parsers support mzML, gzipped mzML, mzXML, MGF, and MS2. Results include spectrum counts by MS level, polarity, centroid or profile mode, retention-time and m/z ranges, precursors, charge and collision-energy summaries, peak and intensity statistics, TIC and BPC previews, ion mobility, DIA windows, and parser warnings.

OpenMassSpec is enabled by default for direct metadata extraction from supported vendor acquisitions. Set `SPECTARR_INSTALL_OPENMASSSPEC=false` before building if you want the smaller extractor image. Built-in parsers handle open formats, and MSConvert remains the conversion compatibility baseline when a vendor reader cannot parse an acquisition.

See [extractor documentation](services/extractor/README.md).

## Automatic instrument uploads

The standalone acquisition agent runs on Windows or Linux beside an instrument computer or file server. It uses conservative polling, waits for stable completed acquisitions, treats native vendor directories atomically, rejects symlinks, blocks temporary and lock files, and keeps an offline SQLite queue. Uploads are resumable, checksummed, idempotent, and deduplicated.

See [agent installation and service guidance](services/agent/README.md).

## Automation and integrations

Automation rules can be global, project-scoped, or instrument-scoped. Rules can request metadata extraction and reproducible conversion after a source becomes ready. The outbox supplies stable artifact events for Searcharr and signed webhooks. Webhook delivery happens in a separate retrying worker.

Spectarr ships with revisioned Standard mzML and Standard MGF processing profiles. Standard mzML is selected by the default desired-output rule on a fresh installation. Additional profiles can use typed MSConvert settings or the Sage, Biosaur, BlitzFF, Casanovo, and Casanovo MGF named configs packaged by MSCLI. Each queued job snapshots its profile revision and parameters for reproducibility.

Processing batches can target an entire project, selected experiments, or selected runs. Preview reports missing, current, outdated, already queued, and incompatible targets before any work is created. Missing-only, missing-and-outdated, and forced modes share the same idempotent conversion queue. The Processing dashboard provides aggregate progress, item errors, cancellation, and retry. Desired-output rules reconcile existing files when a policy or profile changes and reconcile Inbox files after project assignment.

## SDRF and repository submission metadata

Every project can own one revisioned SDRF v1.1.0 document. Spectarr preserves the table as ordered columns and rows, including repeated template and associated-file columns that cannot be represented safely as a JSON object. Imported rows map back to samples, runs, and primary artifacts. Runs support several labeled, pooled, or otherwise multiplexed samples through ordered sample links.

The Metadata and SDRF project page supports generation from stored runs, lossless TSV import and export, table editing, official template selection, structural validation, optional ontology validation, and mapping diagnostics. Automatic generation uses extracted instrument and acquisition metadata where possible. Unknown biological facts remain explicit SDRF reserved values for a scientist to complete.

The release API image includes the official `sdrf-pipelines` validator with ontology support. A valid project can export a repository package containing the SDRF TSV, human-readable source and derived filenames, per-file SHA-256 checksums, and a Spectarr provenance manifest. Directory acquisitions retain their native tree inside the package. Package construction uses temporary disk space approximately equal to the selected project data, so deployments should provide adequate space in the container temporary directory.

SDRF REST endpoints live under `/api/v1/projects/{project_id}/sdrf`. MCP exposes the ordered document, repository readiness, confirmed generation, and confirmed validation without direct database access.

## Deletion and storage reclamation

Administrators can delete experiments from a project. The dashboard previews run, source, derivative, and logical byte counts, then requires the exact experiment name. Instrument inbox experiments, experiments targeted by active agents, and experiments with active jobs are protected from deletion.

Administrators and operators can clear derived mzML, mzXML, MGF, and MS2 files by project from Storage. The preview reports reclaimable bytes and skips files used by active jobs. Reclamation never removes source acquisitions. Purged derivative records retain their checksum, parent, processing profile, job history, and extraction provenance, and the same processing profile can regenerate them later.

MCP is read-only by default. Confirmed write tools can request extraction, conversion, annotation, retry, or automation changes only when `SPECTARR_MCP_ALLOW_WRITES=true`.

## Backup and restore verification

Create and verify a backup:

```bash
make backup BACKUP_DIR=/srv/spectarr-backups
make verify-backup BACKUP_DIR=/srv/spectarr-backups/spectarr-YYYYMMDDTHHMMSSZ
```

Test restoration into a new directory and a separate PostgreSQL database:

```bash
make restore-test \
  BACKUP_DIR=/srv/spectarr-backups/spectarr-YYYYMMDDTHHMMSSZ \
  RESTORE_DIR=/srv/spectarr-restore-test \
  RESTORE_DB=spectarr_restore_test
```

The restore test refuses an existing target directory or database. It never replaces the active instance.

## Development and verification

```bash
make test
```

The test targets cover the API and migrations, dashboard behavior and builds, converter, metadata extractor, acquisition agent, webhook worker, and MCP contracts.

See [real-file acceptance testing](docs/acceptance-testing.md) for the fixture matrix and verified scientific summaries.

## Searcharr boundary

Search services can consume versioned APIs, QC results, artifact events, signed webhooks, or the read-only human-readable library. They should keep their own indexes and reference immutable Spectarr artifact IDs and checksums. The `.spectarr/runs/{run-id}.json` manifests provide stable IDs and provenance when a search engine scans the filesystem directly. Search services should not share Spectarr database tables or modify the managed library.

## License

Spectarr is licensed under the Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
