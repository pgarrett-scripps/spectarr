"""Command line entry point for the acquisition agent."""

from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import sys
import threading
from pathlib import Path

from . import __version__
from .api import SpectarrAgentApi
from .config import AgentConfig
from .config import load_config
from .service import AcquisitionAgent
from .state import AgentState


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch instrument output and upload stable acquisitions to Spectarr")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="TOML configuration file")
    parser.add_argument("--watch", action="append", default=None, help="File or directory to watch, repeatable")
    parser.add_argument("--server-url")
    parser.add_argument("--api-key", help="Bootstrap admin key used only for first registration")
    parser.add_argument("--agent-id", help="Pre-enrolled agent ID from the Spectarr dashboard")
    parser.add_argument("--agent-token", help="Pre-enrolled agent token from the Spectarr dashboard")
    parser.add_argument("--state-db", type=Path)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--experiment-id")
    parser.add_argument("--sample-id")
    parser.add_argument("--instrument-id")
    parser.add_argument("--poll-seconds", type=float)
    parser.add_argument("--stability-seconds", type=float)
    parser.add_argument("--chunk-size", type=int)
    parser.add_argument("--once", action="store_true", help="Scan once and drain currently ready uploads")
    parser.add_argument("--dry-run", action="store_true", help="Verify stable acquisitions without network writes")
    parser.add_argument(
        "--reset-registration",
        action="store_true",
        help="Discard the saved agent token and register again with the bootstrap key",
    )
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    parser.add_argument("--windows-service", action="store_true", help=argparse.SUPPRESS)
    return parser


def configure_logging(config: AgentConfig, level_name: str) -> None:
    level = getattr(logging, level_name)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        handler.close()
    root.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)
    if config.log_file:
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            config.log_file,
            maxBytes=config.log_max_bytes,
            backupCount=config.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def run_agent(
    config: AgentConfig,
    *,
    once: bool,
    reset_registration: bool,
    stop_event: threading.Event | None = None,
) -> None:
    state = AgentState(config.state_db)
    try:
        if reset_registration:
            state.clear_registration()
        api = SpectarrAgentApi(config.server_url, config.request_timeout_seconds)
        agent = AcquisitionAgent(config, state, api)
        if once:
            agent.run_once()
        else:
            agent.run_forever(stop_event)
    finally:
        state.close()


def main() -> int:
    args = build_parser().parse_args()
    overrides = {
        "watch_paths": args.watch,
        "server_url": args.server_url,
        "api_key": args.api_key,
        "agent_id": args.agent_id,
        "agent_token": args.agent_token,
        "state_db": args.state_db,
        "log_file": args.log_file,
        "run_id": args.run_id,
        "experiment_id": args.experiment_id,
        "sample_id": args.sample_id,
        "instrument_id": args.instrument_id,
        "poll_interval_seconds": args.poll_seconds,
        "stability_seconds": args.stability_seconds,
        "chunk_size_bytes": args.chunk_size,
        "dry_run": True if args.dry_run else None,
    }
    try:
        config = load_config(args.config, overrides)
        configure_logging(config, args.log_level)
        if args.windows_service:
            from .windows_service import run_as_windows_service

            run_as_windows_service(
                lambda stop_event: run_agent(
                    config,
                    once=False,
                    reset_registration=args.reset_registration,
                    stop_event=stop_event,
                )
            )
        else:
            run_agent(
                config,
                once=args.once,
                reset_registration=args.reset_registration,
            )
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        logging.getLogger(__name__).error("Agent failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
