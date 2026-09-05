from __future__ import annotations

import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib import error, request


API_URL = "http://127.0.0.1:8000"
DATA_ROOT = Path("/data")
RUNTIME_CONFIG_DIRECTORY = ".spectarr"
RUNTIME_SECRETS_FILE = "runtime-secrets.json"
STORAGE_IDENTITY_FILE = "storage-id"
NETWORK_FILESYSTEM_TYPES = frozenset({"cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs", "9p"})


def process_commands() -> list[tuple[str, list[str], bool]]:
    commands = [
        (
            "spectrum reader",
            ["spectarr-spectrum-server", "--host", "127.0.0.1", "--port", "8002"],
            False,
        ),
        (
            "API",
            ["uvicorn", "spectarr.main:app", "--host", "0.0.0.0", "--port", "8000"],
            False,
        ),
        ("converter", ["spectarr-converter-worker"], True),
        ("extractor", ["spectarr-extractor-worker"], False),
        ("webhooks", ["spectarr-webhook-worker"], False),
        ("MCP", ["spectarr-mcp"], False),
    ]

    if os.getenv("SPECTARR_RESTORE_MODE", "false").lower() in {"true", "1"}:
        return commands[:2]
    return commands


def child_identity(run_as_root: bool) -> dict[str, int]:
    if run_as_root or os.geteuid() != 0:
        return {}
    return {
        "user": int(os.getenv("SPECTARR_UID", "1000")),
        "group": int(os.getenv("SPECTARR_GID", "1000")),
    }


def prepare_data_directories(data_root: Path = DATA_ROOT) -> None:
    directories = [
        data_root,
        data_root / "storage",
        data_root / "scratch",
        Path("/tmp/spectarr-home"),
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        uid = int(os.getenv("SPECTARR_UID", "1000"))
        gid = int(os.getenv("SPECTARR_GID", "1000"))
        for directory in directories:
            os.chown(directory, uid, gid)


def _runtime_secret_path(data_root: Path) -> Path:
    configured = os.getenv("SPECTARR_RUNTIME_SECRETS_FILE")
    return Path(configured) if configured else data_root / RUNTIME_CONFIG_DIRECTORY / RUNTIME_SECRETS_FILE


def _load_runtime_secrets(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read persistent runtime secrets from {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Persistent runtime secrets in {path} must be a JSON object")
    resolved: dict[str, str] = {}
    for name in ("SPECTARR_SECRET_KEY", "SPECTARR_WORKER_TOKEN"):
        value = payload.get(name)
        if value is not None:
            if not isinstance(value, str) or not value:
                raise RuntimeError(f"Persistent {name} in {path} is invalid")
            resolved[name] = value
    return resolved


def _write_runtime_secrets(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            json.dump(values, stream, sort_keys=True)
            stream.write("\n")
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)
    if os.geteuid() == 0:
        uid = int(os.getenv("SPECTARR_UID", "1000"))
        gid = int(os.getenv("SPECTARR_GID", "1000"))
        os.chown(path.parent, uid, gid)
        os.chown(path, uid, gid)


def prepare_runtime_secrets(data_root: Path = DATA_ROOT) -> Path:
    path = _runtime_secret_path(data_root)
    persisted = _load_runtime_secrets(path)
    resolved = {
        name: os.getenv(name) or persisted.get(name) or secrets.token_hex(32)
        for name in ("SPECTARR_SECRET_KEY", "SPECTARR_WORKER_TOKEN")
    }
    _write_runtime_secrets(path, resolved)
    os.environ.update(resolved)
    return path


def _container_mount_map(mounts: object, data_root: Path = DATA_ROOT) -> dict[str, str]:
    if not isinstance(mounts, list):
        raise RuntimeError("Docker returned an invalid mount description")
    mapping: dict[str, str] = {}
    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        destination = mount.get("Destination")
        source = mount.get("Source")
        if not isinstance(destination, str) or not destination.startswith("/"):
            continue
        if isinstance(source, str) and source.startswith("/"):
            mapping[destination] = source
    if str(data_root) not in mapping:
        raise RuntimeError(f"Could not find the {data_root} mount in the Spectarr container")
    return mapping


def prepare_docker_mount_map(data_root: Path = DATA_ROOT) -> dict[str, str]:
    configured_map = os.getenv("SPECTARR_DOCKER_MOUNT_MAP")
    if configured_map:
        try:
            mapping = json.loads(configured_map)
        except json.JSONDecodeError as exc:
            raise RuntimeError("SPECTARR_DOCKER_MOUNT_MAP must be a JSON object of paths") from exc
        if not isinstance(mapping, dict):
            raise RuntimeError("SPECTARR_DOCKER_MOUNT_MAP must be a JSON object of paths")
        return mapping
    configured_root = os.getenv("SPECTARR_DOCKER_DATA_ROOT")
    if configured_root:
        mapping = {str(data_root): configured_root}
        os.environ["SPECTARR_DOCKER_MOUNT_MAP"] = json.dumps(mapping, sort_keys=True)
        return mapping
    container_reference = os.getenv("SPECTARR_CONTAINER_ID") or socket.gethostname()
    try:
        completed = subprocess.run(
            ["docker", "inspect", container_reference, "--format", "{{json .Mounts}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        mapping = _container_mount_map(json.loads(completed.stdout), data_root)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, RuntimeError) as exc:
        raise RuntimeError(
            "Could not discover the host Docker mount for /data. Confirm that the Docker socket is "
            "mounted, or set SPECTARR_DOCKER_DATA_ROOT explicitly."
        ) from exc
    os.environ["SPECTARR_DOCKER_MOUNT_MAP"] = json.dumps(mapping, sort_keys=True)
    os.environ["SPECTARR_DOCKER_DATA_ROOT"] = mapping[str(data_root)]
    return mapping


def prepare_docker_data_root(data_root: Path = DATA_ROOT) -> Path:
    return Path(prepare_docker_mount_map(data_root)[str(data_root)])


def _read_identity(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"Could not read the storage identity marker at {path}") from exc
    return value or None


def _write_identity(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")
    if os.geteuid() == 0:
        uid = int(os.getenv("SPECTARR_UID", "1000"))
        gid = int(os.getenv("SPECTARR_GID", "1000"))
        os.chown(path.parent, uid, gid)
        os.chown(path, uid, gid)


def verify_storage_identity(data_root: Path = DATA_ROOT) -> str:
    """Refuse to run against the wrong (or missing) storage root.

    The storage root may live on separate, possibly network, storage. If that
    mount is absent at startup, writing into the empty directory underneath it
    would strand artifacts on the wrong disk and desync the database.
    """

    local_path = data_root / RUNTIME_CONFIG_DIRECTORY / STORAGE_IDENTITY_FILE
    storage_path = data_root / "storage" / RUNTIME_CONFIG_DIRECTORY / STORAGE_IDENTITY_FILE
    local_identity = _read_identity(local_path)
    storage_identity = _read_identity(storage_path)
    if local_identity and storage_identity:
        if local_identity != storage_identity:
            raise RuntimeError(
                f"The storage root at {data_root / 'storage'} belongs to a different Spectarr instance "
                f"({storage_identity}, expected {local_identity}). Mount the correct storage, or delete "
                f"{local_path} to adopt this storage root."
            )
        return local_identity
    if local_identity and not storage_identity:
        raise RuntimeError(
            f"The storage root at {data_root / 'storage'} has no identity marker but this instance "
            f"expects {local_identity}. The storage mount is probably missing or empty. Mount it and "
            f"restart, or delete {local_path} if the storage root was reset on purpose."
        )
    identity = storage_identity or secrets.token_hex(16)
    _write_identity(storage_path, identity)
    _write_identity(local_path, identity)
    return identity


def _filesystem_type_for(path: Path, mounts_text: str) -> str | None:
    best_point = ""
    best_type: str | None = None
    for line in mounts_text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mount_point = fields[1].replace("\\040", " ")
        if not (str(path) == mount_point or str(path).startswith(mount_point.rstrip("/") + "/")):
            continue
        if len(mount_point) >= len(best_point):
            best_point = mount_point
            best_type = fields[2]
    return best_type


def warn_if_database_on_network_filesystem() -> None:
    url = os.getenv("SPECTARR_DATABASE_URL", "")
    match = re.match(r"^sqlite(?:\+\w+)?:///(/.+)$", url)
    if not match:
        return
    try:
        mounts_text = Path("/proc/mounts").read_text(encoding="utf-8")
    except OSError:
        return
    filesystem = _filesystem_type_for(Path(match.group(1)), mounts_text)
    if filesystem in NETWORK_FILESYSTEM_TYPES:
        print(
            f"WARNING: the SQLite database at {match.group(1)} is on a {filesystem} network filesystem. "
            "SQLite locking is unreliable over network filesystems and can corrupt the database. "
            "Keep the database on local disk and move only the storage root to network storage.",
            file=sys.stderr,
            flush=True,
        )


def wait_for_api(api_process: subprocess.Popen, timeout_seconds: float = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if api_process.poll() is not None:
            raise RuntimeError(f"Spectarr API exited with status {api_process.returncode}")
        try:
            with request.urlopen(f"{API_URL}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, error.URLError):
            pass
        time.sleep(0.25)
    raise TimeoutError("Spectarr API did not become healthy within 120 seconds")


def terminate(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and any(process.poll() is None for process in processes):
        time.sleep(0.1)
    for process in processes:
        if process.poll() is None:
            process.kill()


def main() -> int:
    prepare_data_directories()
    verify_storage_identity()
    warn_if_database_on_network_filesystem()
    prepare_runtime_secrets()
    if os.getenv("SPECTARR_RESTORE_MODE", "false").lower() not in {"true", "1"}:
        prepare_docker_mount_map()
    os.environ["HOME"] = "/tmp/spectarr-home"
    os.environ.setdefault("SPECTARR_API_URL", API_URL)
    os.environ.setdefault("SPECTARR_URL", API_URL)
    os.environ.setdefault("SPECTARR_SPECTRUM_READER_URL", "http://127.0.0.1:8002")
    os.environ.setdefault("SPECTARR_MCP_TRANSPORT", "http")
    children: list[tuple[str, subprocess.Popen]] = []
    stopping = False

    def stop(_signal_number, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    commands = process_commands()
    try:
        for name, command, run_as_root in commands[:2]:
            process = subprocess.Popen(command, **child_identity(run_as_root))
            children.append((name, process))
        wait_for_api(children[1][1])
        for name, command, run_as_root in commands[2:]:
            process = subprocess.Popen(command, **child_identity(run_as_root))
            children.append((name, process))
        while not stopping:
            for name, process in children:
                return_code = process.poll()
                if return_code is not None:
                    print(f"Spectarr {name} process exited with status {return_code}", file=sys.stderr)
                    return return_code or 1
            time.sleep(0.5)
        return 0
    finally:
        terminate([process for _name, process in children])


if __name__ == "__main__":
    raise SystemExit(main())
