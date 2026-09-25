"""Read source files without following replaced path components on POSIX."""
import os
import stat
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def directory_handle(path: Path):
    path = path.absolute()
    descriptors = []
    try:
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(fd)
        for part in path.parts[1:]:
            if part in {".", ".."}:
                raise OSError("Unsafe path component")
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        yield fd
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


@contextmanager
def open_source(path: Path):
    if os.name == "posix":
        with directory_handle(path.parent) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("Acquisition member is not a regular file")
                stream = os.fdopen(fd, "rb")
            except BaseException:
                os.close(fd)
                raise
            with stream:
                yield stream
    else:
        for component in (path, *path.parents):
            if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
                raise OSError("Linked acquisition path")
        with path.open("rb") as stream:
            yield stream


def root_identity(path: Path) -> str:
    if os.name != "posix":
        raise OSError("Catalog mode currently requires a qualified Linux filesystem")
    with directory_handle(path) as fd:
        info = os.fstat(fd)
        return f"posix:{info.st_dev}:{info.st_ino}"


def tree_files(root: Path):
    """Enumerate every bundle member without following directory replacements."""
    if os.name != "posix":
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(entry, "is_junction", lambda: False)():
                raise OSError("Linked acquisition member")
            if stat.S_ISDIR(info.st_mode):
                yield from tree_files(entry)
            elif stat.S_ISREG(info.st_mode):
                yield entry
            else:
                raise OSError("Nonregular acquisition member")
        return
    with directory_handle(root) as fd:
        for name in sorted(os.listdir(fd)):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            entry = root / name
            if stat.S_ISLNK(info.st_mode):
                raise OSError("Symbolic link in acquisition bundle")
            if stat.S_ISDIR(info.st_mode):
                yield from tree_files(entry)
            elif stat.S_ISREG(info.st_mode):
                yield entry
            else:
                raise OSError("Nonregular acquisition member")
