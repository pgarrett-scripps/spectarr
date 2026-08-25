"""JSON command line entry point for a Spectarr conversion worker."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .models import ConversionRequest
from .service import ConversionService, PINNED_DEFAULT_IMAGE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one structured Spectarr conversion job")
    parser.add_argument("request", help="JSON request file, or - for standard input")
    parser.add_argument("--scratch-root", default=os.getenv("SPECTARR_SCRATCH_ROOT", "/var/lib/spectarr/scratch"))
    parser.add_argument("--source-root", action="append", default=None)
    parser.add_argument("--image", default=os.getenv("SPECTARR_MSCONVERT_IMAGE", PINNED_DEFAULT_IMAGE))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    raw = sys.stdin.read() if args.request == "-" else Path(args.request).read_text(encoding="utf-8")
    request = ConversionRequest.from_dict(json.loads(raw))
    roots = args.source_root or os.getenv("SPECTARR_SOURCE_ROOTS", "/data").split(os.pathsep)
    service = ConversionService(
        scratch_root=Path(args.scratch_root),
        allowed_source_roots=tuple(Path(root) for root in roots),
        image=args.image,
    )
    result = service.convert(request)
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0 if result.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
