# Spectarr installation

Spectarr runs as one published container. It does not require a source checkout, a separate database, or a manually configured environment file.

The image targets Linux x86-64 because the vendor-compatible ProteoWizard converter is x86-64. The acquisition agent runs on supported Windows and Linux instrument computers.

## Quick start with Compose

Place `compose.yaml` in a new directory, then run:

```bash
docker compose up -d
```

Open `http://localhost:3280` and create the first administrator account. Compose creates local `data` and `imports` directories automatically. Spectarr generates independent application and worker secrets on first startup and stores them in `data/.spectarr/runtime-secrets.json`.

The standalone Compose file is published at:

```text
https://github.com/pgarrett-scripps/spectarr/releases/latest/download/compose.yaml
```

No setting in `.env.example` is required. Copy it to `.env` only when you want to change ports, paths, identity mapping, authentication behavior, or another advanced option.

## Deployment details

Compose runs exactly one container. That container serves the dashboard and API on port 8000, serves MCP on port 8001, and supervises the internal workers. All durable state is below `SPECTARR_DATA_DIR`. Metadata is stored in `spectarr.db`, artifacts are stored below `storage`, and generated secrets are stored below `.spectarr`.

Published ports bind to `127.0.0.1` by default. Put the dashboard behind an HTTPS reverse proxy for network access. Do not expose the Docker socket or MCP port to untrusted networks.

The converter requires Docker access to run the vendor-compatible ProteoWizard image. A rootless Docker daemon is recommended. Set `DOCKER_SOCKET` when its socket is not `/var/run/docker.sock`. Spectarr normally discovers the host path or named volume mounted at `/data`. Set `SPECTARR_DOCKER_DATA_ROOT` only if discovery is unavailable in a custom container runtime.

Production startup rejects weak explicitly configured secrets and wildcard CORS. Password login has persistent throttling. Most processes run with the configured unprivileged UID. The converter process retains the Docker socket access needed to launch conversion jobs.

Create and verify a backup from the bundle directory:

```bash
export SPECTARR_COMPOSE_FILE=compose.yaml
export SPECTARR_DATA_DIR=./data
scripts/backup.sh /srv/spectarr-backups
scripts/verify-backup.sh /srv/spectarr-backups/spectarr-YYYYMMDDTHHMMSSZ
```

Use `scripts/restore-test.sh` with a new directory before relying on a backup operationally.
