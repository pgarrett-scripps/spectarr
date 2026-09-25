"""Coordinate storage reservations between download slots and API processes."""
import shutil

from sqlalchemy import select

from .database import SessionLocal
from .locking import file_lock
from .models import RemoteImport


class CapacityBusy(ValueError):
    """Capacity is currently reserved by another download."""


def item_lock(storage, import_id):
    return file_lock(storage.internal / "remote-locks" / f"{import_id}.lock", exclusive=True)


def reserve(storage, import_id, size, offset, copies):
    with file_lock(storage.internal / "remote-capacity.lock", exclusive=True, blocking=True), SessionLocal() as session:
        others = list(session.scalars(select(RemoteImport).where(RemoteImport.reserved_bytes > 0, RemoteImport.id != import_id)))
        reserved = 0
        for item in others:
            try:
                with item_lock(storage, item.id):
                    # A terminated owner cannot retain a reservation.
                    item.reserved_bytes = 0
            except BlockingIOError:
                reserved += item.reserved_bytes
        required = max(0, copies * size - offset)
        free_bytes = shutil.disk_usage(storage.staging).free
        if free_bytes < reserved + required + 256 * 1024 * 1024:
            session.commit()
            if reserved and free_bytes >= required + 256 * 1024 * 1024:
                raise CapacityBusy("Waiting for disk space reserved by other active downloads")
            raise ValueError("Insufficient disk space for this file and other active downloads")
        item = session.get(RemoteImport, import_id)
        if item is None:
            raise ValueError("Import was removed")
        item.reserved_bytes = required
        session.commit()


def release(storage, import_id):
    with file_lock(storage.internal / "remote-capacity.lock", exclusive=True, blocking=True), SessionLocal() as session:
        if item := session.get(RemoteImport, import_id):
            item.reserved_bytes = 0
            session.commit()
