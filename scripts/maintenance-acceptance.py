"""Run inside a disposable container to verify idle workers survive publication."""

import json
import os
import time
from pathlib import Path
from urllib import error, request



def converter_processes() -> set[str]:
    result = set()
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            arguments = path.read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError):
            continue
        if any(argument.endswith(b"/spectarr-converter-worker") for argument in arguments):
            result.add(path.parent.name)
    return result


def main() -> None:
    if os.environ.get("SPECTARR_ACCEPTANCE_TEST") != "1":
        raise SystemExit("Run only in a disposable acceptance container")

    secrets = json.loads(Path("/data/.spectarr/runtime-secrets.json").read_text())
    os.environ.update(secrets)

    from spectarr.database import SessionLocal
    from spectarr.library_publication import LibraryPublication
    from spectarr.locking import maintenance_lock
    from spectarr.storage import LocalArtifactStorage

    headers = {"X-Spectarr-Worker-Token": secrets["SPECTARR_WORKER_TOKEN"]}
    url = "http://127.0.0.1:8000/api/v1/jobs"
    deadline = time.monotonic() + 60
    while True:
        with request.urlopen(request.Request(url, headers=headers)) as response:
            busy = any(job["state"] in {"queued", "running"} for job in json.load(response))
        if not busy:
            break
        assert time.monotonic() < deadline, "Processing did not become idle"
        time.sleep(1)
    before = converter_processes()
    assert len(before) == 1
    storage = LocalArtifactStorage(Path("/data/storage"))
    with maintenance_lock(storage.root, exclusive=True), SessionLocal() as session:
        publication = LibraryPublication(storage)
        publication.begin()
        try:
            try:
                request.urlopen(request.Request(url, headers=headers))
            except error.HTTPError as response:
                assert response.code == 503
            else:
                raise AssertionError("Expected maintenance response")
            time.sleep(8)
            assert converter_processes() == before
        finally:
            publication.recover(session)
    with request.urlopen(request.Request(url, headers=headers)) as response:
        assert response.status == 200
    assert converter_processes() == before
    print(json.dumps({"status": "ok", "maintenance_response": 503, "converter_restarted": False}))


if __name__ == "__main__":
    main()
