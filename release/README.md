# Install Spectarr

Spectarr runs on a Linux x86-64 server with Docker Engine and Docker Compose v2. No source checkout, separate database, or environment file is required.

## Start with Compose

Run these commands in a new directory on the server:

```bash
mkdir spectarr && cd spectarr
curl -fL -o compose.yaml https://github.com/pgarrett-scripps/spectarr/releases/latest/download/compose.yaml
docker compose up -d --wait
```

If you extracted the release archive, open that directory and run only `docker compose up -d --wait`. The first download can take a while. `--wait` returns when the container is healthy, rather than just started.

Open [http://localhost:3280](http://localhost:3280). Create the first administrator with a password of at least 12 characters. Keep this initial setup private until the account exists.

The installation creates `data/` and `imports/`. It generates independent secrets in `data/.spectarr/runtime-secrets.json` and preserves them across container replacement. No default password or manually generated key is needed.

Use the [first-run walkthrough](https://github.com/pgarrett-scripps/spectarr/blob/main/docs/getting-started.md) to import the small demo file and view a spectrum.

## Access a remote server

The default port is reachable only on the server itself. For personal access, open a tunnel from your computer:

```bash
ssh -N -L 3280:127.0.0.1:3280 user@your-server
```

Leave that terminal running, then open [http://localhost:3280](http://localhost:3280) on your computer. Replace `user@your-server` with your SSH login. This also works for the first administrator setup.

If port 3280 is busy on your computer, use `-L 3281:127.0.0.1:3280` and browse to `http://localhost:3281` instead.

## HTTPS access for a team

Use a reverse proxy on the same server, with a hostname and certificate you control. For example, a host-installed Caddy instance can proxy to the default loopback port:

```caddyfile
spectarr.example.org {
    reverse_proxy 127.0.0.1:3280
}
```

Create `.env` next to `compose.yaml`, substituting your real hostname and origin:

```dotenv
SPECTARR_TRUSTED_HOSTS=["localhost","spectarr.example.org"]
SPECTARR_CORS_ORIGINS=["https://spectarr.example.org"]
```

Then apply the settings:

```bash
docker compose up -d --wait
```

The hostname allowlist and exact origin are both needed. The explicit origin also handles TLS termination when the backend sees an HTTP connection from the proxy. Preserve the browser's Host header. Do not use wildcard hosts or origins.

This example assumes the proxy runs on the host. A proxy in another container needs a shared Docker network and must reach `spectarr:8000` there. Its own `127.0.0.1` is not the Spectarr container.

For direct access over a trusted private network, set `SPECTARR_BIND_ADDRESS` to the server's private IP and keep password authentication enabled. HTTP does not encrypt passwords, so use HTTPS or the SSH tunnel for normal access. Literal IP addresses are accepted. Add named LAN hosts to `SPECTARR_TRUSTED_HOSTS`.

MCP remains on loopback independently through `SPECTARR_MCP_BIND_ADDRESS=127.0.0.1`. Publishing the dashboard does not require publishing MCP. See the [MCP guide](https://github.com/pgarrett-scripps/spectarr/blob/main/docs/conversion-and-mcp.md) when connecting clients.

## Storage and permissions

By default, all durable state lives in `./data`: the SQLite database, generated secrets, original acquisitions, derivatives, and readable library. `./imports` is a read-only source directory for server-path imports. Importing from it copies data into managed storage.

Optional `.env` settings:

```dotenv
SPECTARR_DATA_DIR=/srv/spectarr/data
SPECTARR_STORAGE_DIR=/mnt/lab-data/spectarr
SPECTARR_IMPORT_DIR=/mnt/acquisitions
SPECTARR_UID=1000
SPECTARR_GID=1000
```

Omit `SPECTARR_STORAGE_DIR` to keep artifacts under the data directory. Keep `SPECTARR_DATA_DIR` on a local filesystem. SQLite locking is unreliable over CIFS and NFS. Use separate bulk storage for large files, and ensure the configured UID and GID can write there. `id -u` and `id -g` show your account's IDs.

For an existing installation, back up, stop the app, and migrate the existing database, secrets, and artifact tree before changing paths. Pointing at an empty directory creates an empty installation. The storage identity marker detects a missing or mismatched previously enrolled artifact mount. Restore the correct mount if startup reports an identity mismatch.

Sizing depends on acquisition size and concurrent conversion work. Leave space for originals, derivatives, conversion scratch files, and backups. A small MGF demo does not represent vendor-processing resource needs.

## Docker and vendor conversion

The main container serves the dashboard, API, reader, and background workers. The converter launches separate ProteoWizard containers through the mounted Docker socket. Socket access gives that process control of its Docker daemon. Use a trusted server. A rootless Docker daemon limits that control to the daemon's user.

For an existing rootless daemon, set its actual socket path in `.env`:

```dotenv
DOCKER_SOCKET=/run/user/1000/docker.sock
```

The socket must already exist. Compose now fails immediately on a missing path instead of creating a directory in its place. Spectarr discovers its storage mounts through Docker. Set `SPECTARR_DOCKER_DATA_ROOT` only for custom runtimes where discovery is unavailable.

The default ProteoWizard image name indicates agreement to its vendor licenses. Review those terms before vendor conversion. The published release notes describe known vendor-image security limitations. An ARM server or Docker-compatible alternative is not part of the tested deployment target.

## Optional native converter

The release archive includes `compose.native.yaml`. To enable it, set `SPECTARR_MSCONVERT_RS_BINARY` in `.env` to an absolute host path for a Linux x86-64 `msconvert-rs` executable compatible with Debian Trixie. Start with:

```bash
docker compose -f compose.yaml -f compose.native.yaml up -d
docker compose -f compose.yaml -f compose.native.yaml exec spectarr /opt/native/msconvert-rs capabilities --json
```

The binary is mounted read-only and is not downloaded automatically. Native recipes must explicitly select `converter: "msconvert-rs"` through the API. ProteoWizard remains the default. Native conversion remains experimental for some vendor modes. The included `native-conversion.md` describes supported recipes and preservation policies.

## Update or stop

First create and verify a backup. Then, from the installation directory:

```bash
docker compose pull
docker compose up -d --wait
```

The default image tag is `latest`. To pin a version, set `SPECTARR_VERSION` in `.env` to the version of the release you downloaded, or set `SPECTARR_IMAGE_REF` to a complete image reference with a digest. Read the target release notes before updating.

Image updates do not replace your local Compose file. Review configuration changes in each release and merge them into your installation.

To stop the app while keeping its data:

```bash
docker compose down
```

Keep the installation's `data/` directory and any separate storage mount. To roll back after a database migration, restore the backup matching the older image rather than starting that image against a newer database.

## Backups

For scheduled backups, download the full [release archive](https://github.com/pgarrett-scripps/spectarr/releases/latest), choose an existing backup destination, and set `SPECTARR_BACKUP_DIR` in `.env`. Start with the included override:

```bash
docker compose -f compose.yaml -f compose.backups.yaml up -d --wait
```

Enable scheduling and retention under **Settings → Backups**. Use the same pair of Compose files for future updates so the backup mount remains attached.

The archive also includes scripts for a manual verified backup:

```bash
export SPECTARR_COMPOSE_FILE=compose.yaml
export SPECTARR_DATA_DIR=./data
scripts/backup.sh /srv/spectarr-backups
scripts/verify-backup.sh /srv/spectarr-backups/spectarr-YYYYMMDDTHHMMSSZ
```

Replace the timestamp with the created backup directory. Snapshots include the database, artifact storage, image reference, and checksums. Mutations temporarily receive retryable HTTP 503 responses while a snapshot is taken. Rehearse an independent restoration before relying on backups.

See [backup integration](https://github.com/pgarrett-scripps/spectarr/blob/main/docs/backup-integration.md) and [recovery](https://github.com/pgarrett-scripps/spectarr/blob/main/docs/reliability-and-recovery.md). Both guides are also included in the release archive.

## Troubleshooting

Start with:

```bash
docker compose ps
docker compose logs --tail=100 spectarr
```

| Symptom | What to check |
| --- | --- |
| Cannot open the app from another computer | Use the SSH tunnel above. `localhost` in your browser means your own computer. |
| `Untrusted request host` | Add the exact DNS hostname to `SPECTARR_TRUSTED_HOSTS` and recreate the container. |
| `Untrusted browser origin` behind HTTPS | Set the exact HTTPS origin in `SPECTARR_CORS_ORIGINS` and preserve Host at the proxy. |
| Docker socket path is missing | Verify Docker is running and `DOCKER_SOCKET` points to its existing socket. |
| Permission denied under `/data` | Check the configured UID and GID and the host directory or network-share permissions. |
| Storage identity mismatch | Restore the expected storage mount before restarting. Do not remove markers just to bypass the check. |
| Port already in use | Set `SPECTARR_PORT` or `SPECTARR_MCP_PORT` to a free port in `.env`. |
| Unknown `--wait` option | Update to a current Docker Compose v2 plugin. |
| Conversion is slow on first use | Docker may still be pulling the vendor image. Inspect worker logs and available disk space. |

[All documentation](https://github.com/pgarrett-scripps/spectarr/blob/main/docs/README.md) · [Report a problem](https://github.com/pgarrett-scripps/spectarr/issues)
