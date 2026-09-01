from __future__ import annotations

import json
import os
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


def process_commands() -> list[tuple[str, list[str], bool]]:
    return [
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


def _data_mount_source(mounts: object, destination: Path = DATA_ROOT) -> Path:
    if not isinstance(mounts, list):
        raise RuntimeError("Docker returned an invalid mount description")
    for mount in mounts:
        if not isinstance(mount, dict) or mount.get("Destination") != str(destination):
            continue
        source = mount.get("Source")
        if isinstance(source, str) and source.startswith("/"):
            return Path(source)
    raise RuntimeError(f"Could not find the {destination} mount in the Spectarr container")


def prepare_docker_data_root(data_root: Path = DATA_ROOT) -> Path:
    configured = os.getenv("SPECTARR_DOCKER_DATA_ROOT")
    if configured:
        return Path(configured)
    container_reference = os.getenv("SPECTARR_CONTAINER_ID") or socket.gethostname()
    try:
        completed = subprocess.run(
            ["docker", "inspect", container_reference, "--format", "{{json .Mounts}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        source = _data_mount_source(json.loads(completed.stdout), data_root)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, RuntimeError) as exc:
        raise RuntimeError(
            "Could not discover the host Docker mount for /data. Confirm that the Docker socket is "
            "mounted, or set SPECTARR_DOCKER_DATA_ROOT explicitly."
        ) from exc
    os.environ["SPECTARR_DOCKER_DATA_ROOT"] = str(source)
    return source


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
    prepare_runtime_secrets()
    prepare_docker_data_root()
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
