#!/usr/bin/env python3
"""Capture exact working sources, including local dependency changes, for a candidate."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *arguments])


def capture(root: Path, output: Path) -> dict:
    names = sorted(set(git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
                       .decode().rstrip("\0").split("\0")))
    records = []
    archive_path = output / f"{root.name}.tar.gz"
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for name in names:
                    path = root / name
                    if not path.exists() and not path.is_symlink():
                        continue
                    if not path.is_file() and not path.is_symlink():
                        continue
                    if path.is_symlink():
                        raise ValueError(f"Review symlink before packaging: {path}")
                    content = path.read_bytes()
                    info = tarfile.TarInfo(f"{root.name}/{name}")
                    info.mode = path.stat().st_mode & 0o777
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
                    records.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(),
                                    "bytes": len(content), "mode": info.mode})
    return {
        "repository": root.name,
        "head": git(root, "rev-parse", "HEAD").decode().strip(),
        "changes": git(root, "status", "--porcelain").decode().splitlines(),
        "archive": archive_path.name,
        "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "files": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--image", required=True, help="Exact locally tested image ID")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    repositories = [root, root.parent / "msconvert-cli", root.parent / "mzmlpy", root.parent / "spxtacular"]
    manifest = {"image": args.image, "repositories": [capture(path, args.output) for path in repositories]}
    args.output.joinpath("sources.json").write_text(json.dumps(manifest, indent=2) + "\n")
    args.output.joinpath("IMAGE").write_text(args.image + "\n")
    paths = sorted(args.output.iterdir())
    args.output.joinpath("SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in paths))
    print(args.output)


if __name__ == "__main__":
    main()
