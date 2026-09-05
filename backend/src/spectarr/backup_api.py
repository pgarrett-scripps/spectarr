from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_admin, require_request_access
from .backup_service import BackupPolicy, BackupService


router = APIRouter(prefix="/backups", tags=["backups"], dependencies=[Depends(require_request_access), Depends(require_admin)])


def invoke(method, *args):
    try:
        return method(*args)
    except BlockingIOError as error:
        raise HTTPException(409, "A backup operation is already running") from error
    except (OSError, ValueError, KeyError) as error:
        raise HTTPException(409, str(error)) from error


@router.get("")
def backup_status() -> dict:
    return invoke(BackupService().status)


@router.put("/policy")
def update_backup_policy(policy: BackupPolicy) -> dict:
    return invoke(BackupService().configure, policy)


@router.post("/run", status_code=202)
def request_backup() -> dict:
    return invoke(BackupService().request, "backup")


@router.post("/restore-check", status_code=202)
def request_restore_check() -> dict:
    return invoke(BackupService().request, "restore")
