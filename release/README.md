# Spectarr release installation

This bundle runs published, versioned Spectarr images. It does not require a source checkout or a separate MSCLI repository.

Current container images target Linux x86-64 because the vendor-compatible ProteoWizard conversion image is x86-64. The acquisition-agent wheel runs on supported Windows and Linux instrument computers.

1. Copy `.env.example` to `.env`.
2. Generate independent database, worker, and application secrets with a password manager or `openssl rand -hex 32`.
3. Set `SPECTARR_DOCKER_DATA_ROOT` to the absolute host path of the data directory.
4. Create `data` and `imports` directories owned by `SPECTARR_UID` and `SPECTARR_GID`.
5. Run `docker compose config --quiet` to validate the configuration.
6. Start the stack with `docker compose pull` followed by `docker compose up -d`.
7. Open the dashboard and create the first administrator.

Published ports bind to `127.0.0.1` by default. Put the dashboard behind an HTTPS reverse proxy for network access. Back up both the PostgreSQL volume and `SPECTARR_DATA_DIR`. Do not expose the Docker socket or MCP port to untrusted networks.

The converter requires Docker access to run the vendor-compatible ProteoWizard image. A rootless Docker daemon is recommended. Set `DOCKER_SOCKET` when its socket is not `/var/run/docker.sock`.

Production startup rejects SQLite, weak application or worker secrets, the default database password, and wildcard CORS. Password login has persistent throttling. The release images run the API as an unprivileged UID and include build provenance and SBOM metadata.

Create and verify a backup from the bundle directory:

```bash
export SPECTARR_COMPOSE_FILE=compose.yaml
export SPECTARR_ENV_FILE=.env
export SPECTARR_DATA_DIR=/absolute/path/to/spectarr/data
scripts/backup.sh /srv/spectarr-backups
scripts/verify-backup.sh /srv/spectarr-backups/spectarr-YYYYMMDDTHHMMSSZ
```

Use `scripts/restore-test.sh` with a new directory and database name before relying on a backup operationally.
