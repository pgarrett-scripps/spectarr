from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib import error, request


API_URL = "http://127.0.0.1:8000"


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


def prepare_data_directories() -> None:
    directories = [
        Path("/data"),
        Path("/data/storage"),
        Path("/data/scratch"),
        Path("/tmp/spectarr-home"),
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        uid = int(os.getenv("SPECTARR_UID", "1000"))
        gid = int(os.getenv("SPECTARR_GID", "1000"))
        for directory in directories:
            os.chown(directory, uid, gid)


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
