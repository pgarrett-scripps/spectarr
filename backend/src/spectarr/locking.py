from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path, *, exclusive: bool, blocking: bool = False):
    """Process-owned locks are released by the OS even after forced termination."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(handle.fileno(), operation | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def maintenance_lock(root: Path, *, exclusive: bool, blocking: bool = False):
    return file_lock(root / ".spectarr" / "maintenance.lock", exclusive=exclusive, blocking=blocking)
