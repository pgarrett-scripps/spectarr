"""Webhook worker command line entry point."""

from __future__ import annotations

import argparse
import logging

from .config import WorkerConfig
from .worker import build_worker


def main() -> int:
    parser = argparse.ArgumentParser(description="Deliver signed Spectarr webhooks")
    parser.add_argument("--once", action="store_true", help="Process at most one ready delivery")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        worker = build_worker(WorkerConfig.from_environment())
        if args.once:
            worker.process_one()
        else:
            worker.run_forever()
    except KeyboardInterrupt:
        return 130
    except Exception as worker_error:
        logging.getLogger(__name__).error("Webhook worker failed: %s", worker_error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
