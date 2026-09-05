"""Boot a restored API in a separate process without network listeners or workers."""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path


def main() -> int:
    database, storage = (Path(value).resolve() for value in sys.argv[1:])
    worker_token = secrets.token_urlsafe(32)
    os.environ.update({
        "SPECTARR_DATABASE_URL": f"sqlite:///{database}",
        "SPECTARR_STORAGE_ROOT": str(storage),
        "SPECTARR_LIBRARY_ROOT": str(storage / "library"),
        "SPECTARR_RESTORE_MODE": "true",
        "SPECTARR_BACKUP_ROOT": "",
        "SPECTARR_ENVIRONMENT": "production",
        "SPECTARR_AUTH_MODE": "password",
        "SPECTARR_AUTH_ENABLED": "true",
        "SPECTARR_WORKER_TOKEN": worker_token,
        "SPECTARR_SECRET_KEY": secrets.token_urlsafe(32),
        "SPECTARR_CORS_ORIGINS": "[]",
        "SPECTARR_API_PREFIX": "/api/v1",
    })
    from fastapi.testclient import TestClient
    from .main import app

    with TestClient(app) as client:
        response = client.get("/api/v1/system/health", headers={"X-Spectarr-Worker-Token": worker_token})
        response.raise_for_status()
        if response.json().get("database") != "ok" or response.json().get("storage") != "ok":
            raise RuntimeError("Restored database or storage is unhealthy")
        response = client.post("/api/v1/projects", json={"name": "must remain read only"}, headers={"X-Spectarr-Worker-Token": worker_token})
        if response.status_code != 503:
            raise RuntimeError("Restored API did not enforce restore verification mode")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
