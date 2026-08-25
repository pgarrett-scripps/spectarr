#!/usr/bin/env python3
"""Verify that every distributable surface matches the canonical version."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYPROJECTS = (
    ROOT / "backend/pyproject.toml",
    ROOT / "services/agent/pyproject.toml",
    ROOT / "services/converter/pyproject.toml",
    ROOT / "services/extractor/pyproject.toml",
    ROOT / "services/mcp/pyproject.toml",
    ROOT / "services/webhooks/pyproject.toml",
)
INIT_FILES = (
    ROOT / "backend/src/spectarr/__init__.py",
    ROOT / "services/agent/src/spectarr_agent/__init__.py",
    ROOT / "services/converter/src/spectarr_converter/__init__.py",
    ROOT / "services/extractor/src/spectarr_extractor/__init__.py",
    ROOT / "services/mcp/src/spectarr_mcp/__init__.py",
    ROOT / "services/webhooks/src/spectarr_webhooks/__init__.py",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Optional release tag such as v0.1.0")
    args = parser.parse_args()
    expected = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    failures: list[str] = []

    for path in PYPROJECTS:
        version = tomllib.loads(path.read_text(encoding="utf-8"))["project"]["version"]
        if version != expected:
            failures.append(f"{path.relative_to(ROOT)} has {version}")

    pattern = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
    for path in INIT_FILES:
        match = pattern.search(path.read_text(encoding="utf-8"))
        version = match.group(1) if match else "missing"
        if version != expected:
            failures.append(f"{path.relative_to(ROOT)} has {version}")

    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    for label, version in (
        ("frontend/package.json", package["version"]),
        ("frontend/package-lock.json", lock["version"]),
        ("frontend/package-lock.json root package", lock["packages"][""]["version"]),
    ):
        if version != expected:
            failures.append(f"{label} has {version}")

    release_version = next(
        line.split("=", 1)[1]
        for line in (ROOT / "release/.env.example").read_text(encoding="utf-8").splitlines()
        if line.startswith("SPECTARR_VERSION=")
    )
    if release_version != expected:
        failures.append(f"release/.env.example has {release_version}")

    if args.tag and args.tag.removeprefix("v") != expected:
        failures.append(f"release tag {args.tag} does not match v{expected}")

    if failures:
        print("Version validation failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print(f"All distributable surfaces match {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
