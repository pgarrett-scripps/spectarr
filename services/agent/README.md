# Spectarr acquisition agent

The acquisition agent runs beside an instrument workstation or file server. It watches configured locations, waits for acquisitions to stop changing, verifies their content, and uploads them to Spectarr through resumable API sessions.

The standalone Windows installer includes its own runtime. Source installs support Windows and Linux with Python 3.11 or newer and have no third-party runtime dependencies.

## Safety model

The agent opens acquisition files only for reading. It never renames, moves, deletes, locks, or writes into instrument output folders.

Important behavior:

- A candidate must have the same size, modification metadata, and directory membership for the full stability window.
- Vendor directories such as Bruker `.d` and Waters `.raw` are treated as one bundle.
- A temporary or lock file anywhere in a bundle prevents upload.
- Symbolic links are rejected and never followed.
- SHA-256 is calculated after stability is established. The acquisition is checked again after hashing and after upload.
- Files in a vendor bundle are uploaded independently. Spectarr reconstructs and verifies the native directory bundle without an archive.
- Chunks use exact offsets and stable idempotency keys. Lost responses can be retried safely.
- The server verifies the final file checksum or every bundle member checksum before publishing an immutable artifact.
- The SQLite queue survives reboots and network outages. An interrupted upload is recovered as a resumable retry.
- Server-side checksum matching can finish a session without sending duplicate bytes.

The SQLite queue stores paths and upload state, not copies of acquisitions. Source data must remain available until an item reaches `complete` or `deduplicated`.

Keep the state database on a local disk. SQLite locking is not reliable on every network filesystem.

## Install on Windows

Download `spectarr-agent-<version>-windows-x64.msi` from the Spectarr release. The initial demo installer is unsigned, so Windows may display an unknown-publisher warning. Install it, then open **Configure Spectarr Agent** from the Start menu. Enter the server URL, acquisition folder, agent ID, and one-time token from the Instrument agents dashboard.

The installer registers `SpectarrAgent` with the Windows Service Control Manager. Configuration, queue state, and rotating logs are stored under `C:\ProgramData\Spectarr Agent`. The service runs as `LocalService`. Grant `NT AUTHORITY\LOCAL SERVICE` read access to each acquisition folder.

Run the installed `Upgrade-Agent.ps1` with the new MSI path to upgrade without replacing the configuration or queue. The script verifies the installer signature and restarts the service. Remove the application through Windows Installed apps. Uninstall preserves the configuration, logs, and queue by default.

After rotating a token in the dashboard, run **Configure Spectarr Agent** again with the new token. The configured credential replaces the saved credential when the service restarts.

For a network share, configure a UNC path and a service identity that has read access. Do not use a mapped drive letter.

## Install from source

From this directory:

```bash
python -m venv .venv
.venv/bin/python -m pip install .
```

For Windows development without the installer:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install .
```

Copy `agent.example.toml` to a machine-specific location and update the server, watch paths, state database, and run target.

## Run targeting

Choose one optional override. When neither is set, Spectarr routes acquisitions through the agent's backend-managed inbox:

- No target creates one run per acquisition in the agent's inbox. These runs are marked as needing assignment.
- `experiment_id` creates one run per discovered acquisition directly in that experiment. The acquisition filename becomes the run name. Optional `sample_id` and `instrument_id` are attached.
- `run_id` attaches every uploaded acquisition to one existing run. This is mainly useful for controlled imports.

Automatic instrument upload normally uses the backend-managed inbox. Use `experiment_id` only for an intentionally fixed destination.

## First registration

An administrator can register the agent in the Spectarr dashboard. Configure both one-time values shown by the dashboard:

```bash
export SPECTARR_AGENT_ID="replace-with-agent-id"
export SPECTARR_AGENT_TOKEN="agt_replace-with-agent-token"
spectarr-agent --config /etc/spectarr-agent.toml --once
```

PowerShell:

```powershell
$env:SPECTARR_AGENT_ID = "replace-with-agent-id"
$env:SPECTARR_AGENT_TOKEN = "agt_replace-with-agent-token"
spectarr-agent --config "C:\ProgramData\Spectarr Agent\agent.toml" --once
```

Alternatively, the agent can register itself. The first connection requires an administrator API key:

```bash
export SPECTARR_API_KEY="replace-with-bootstrap-key"
spectarr-agent --config /etc/spectarr-agent.toml --once
```

PowerShell:

```powershell
$env:SPECTARR_API_KEY = "replace-with-bootstrap-key"
spectarr-agent --config "C:\ProgramData\Spectarr Agent\agent.toml" --once
```

Spectarr returns a one-time `agt_` token. The agent stores it in its SQLite database and uses it for later heartbeats and uploads. Remove the administrator key from the environment after registration. Protect the state database as a credential. The agent requests owner-only permissions where the operating system supports them.

If an agent token is revoked, provide a bootstrap key again and run:

```bash
spectarr-agent --config /etc/spectarr-agent.toml --once --reset-registration
```

## Normal operation

Run continuously:

```bash
spectarr-agent --config /etc/spectarr-agent.toml
```

Run one polling cycle and drain ready queue items:

```bash
spectarr-agent --config /etc/spectarr-agent.toml --once
```

Verify discovery and checksums without registration or upload:

```bash
spectarr-agent --config /etc/spectarr-agent.toml --once --dry-run --log-level INFO
```

Dry-run mode updates local stability observations, but it does not queue uploads, contact Spectarr, or modify acquisitions.

CLI options override TOML. Environment variables override TOML and are overridden by CLI options. Common variables include:

- `SPECTARR_URL`
- `SPECTARR_API_KEY`
- `SPECTARR_AGENT_WATCH_PATHS`, separated with the operating system path separator
- `SPECTARR_AGENT_STATE_DB`
- `SPECTARR_AGENT_EXPERIMENT_ID`
- `SPECTARR_AGENT_INSTRUMENT_ID`
- `SPECTARR_AGENT_STABILITY_SECONDS`
- `SPECTARR_AGENT_CHUNK_SIZE`

## Service operation

On Linux, run the command under systemd with a dedicated unprivileged account. Grant that account read-only access to instrument exports and write access only to the directory containing `queue.sqlite3`.

On Windows, use the release MSI. It contains a native Service Control Manager host and does not require Python, Docker, WinSW, or NSSM. Grant the service identity read access to acquisition folders and modify access only to `C:\ProgramData\Spectarr Agent`.

Use HTTPS when the agent connects across a network. Do not expose the administrator bootstrap key in command history or service arguments.

## Completion detection

Polling is intentional. Instrument and network filesystems often provide incomplete or unreliable change notifications. Each poll computes a lightweight signature from file size and nanosecond modification time. Bundle signatures include every relative file path, size, and modification time.

The default two-minute stability window is conservative. Increase it for instruments that pause for long periods while writing. The agent also waits whenever these patterns appear:

```text
*.tmp  *.temp  *.partial  *.part  *.lock  *.lck
*.download  *.crdownload  *.inprogress  ~*  .~lock.*
```

## API contract

The agent uses these versioned endpoints:

```text
POST  /api/v1/agents/register
POST  /api/v1/agents/{id}/heartbeat
POST  /api/v1/upload-sessions
GET   /api/v1/upload-sessions/{id}
PATCH /api/v1/upload-sessions/{id}
PATCH /api/v1/upload-sessions/{id}/files/{relative_path}
POST  /api/v1/upload-sessions/{id}/complete
```

Single files use one offset. Bundle status contains an independent offset for every manifest path. Offset conflicts return the server's expected `Upload-Offset`, allowing the agent to recover after a connection drops between server commit and client response.

## Tests

```bash
python -m pytest
```

The suite covers configuration, ignored patterns, stability windows, symlink rejection, immutable hashing, SQLite migration and recovery, occurrence identity, checksum storage deduplication, credential rotation, registration, exact HTTP headers, file resume, native bundle resume, offline retry, heartbeat, and dry-run behavior.
