"""PyInstaller entry point for the Windows acquisition agent."""

from spectarr_agent.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
