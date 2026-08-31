# Spectarr release installation

This bundle runs one published Spectarr container. It does not require a source checkout or a separate MSCLI repository.

The image targets Linux x86-64 because the vendor-compatible ProteoWizard converter is x86-64. The acquisition agent runs on supported Windows and Linux instrument computers.

1. Copy `.env.example` to `.env`.
2. Generate independent worker and application secrets with a password manager or `openssl rand -hex 32`.
3. Set `SPECTARR_DATA_DIR` and `SPECTARR_DOCKER_DATA_ROOT` to the same absolute host data directory.
4. Create the data and imports directories owned by `SPECTARR_UID` and `SPECTARR_GID`.
5. Run `docker compose config --quiet` to validate the configuration.
6. Start Spectarr with `docker compose pull` followed by `docker compose up -d`.
7. Open the dashboard and create the first administrator.

Compose runs exactly one container. That container serves the dashboard and API on port 8000, serves MCP on port 8001, and supervises the internal workers. All durable state is below `SPECTARR_DATA_DIR`. Metadata is stored in `spectarr.db`, and artifacts are stored below `storage`.

Published ports bind to `127.0.0.1` by default. Put the dashboard behind an HTTPS reverse proxy for network access. Do not expose the Docker socket or MCP port to untrusted networks.

The converter requires Docker access to run the vendor-compatible ProteoWizard image. A rootless Docker daemon is recommended. Set `DOCKER_SOCKET` when its socket is not `/var/run/docker.sock`.

Production startup rejects weak application or worker secrets and wildcard CORS. Password login has persistent throttling. Most processes run with the configured unprivileged UID. The converter process retains the Docker socket access needed to launch conversion jobs.

Create and verify a backup from the bundle directory:

```bash
export SPECTARR_COMPOSE_FILE=compose.yaml
export SPECTARR_ENV_FILE=.env
export SPECTARR_DATA_DIR=/absolute/path/to/spectarr/data
scripts/backup.sh /srv/spectarr-backups
scripts/verify-backup.sh /srv/spectarr-backups/spectarr-YYYYMMDDTHHMMSSZ
```

Use `scripts/restore-test.sh` with a new directory before relying on a backup operationally.
