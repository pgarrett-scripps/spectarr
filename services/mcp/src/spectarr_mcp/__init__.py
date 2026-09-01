"""Spectarr MCP server package."""

__version__ = "0.2.0"

from .api import SpectarrApiClient
from .server import SpectarrMcpServer

__all__ = ["SpectarrApiClient", "SpectarrMcpServer", "__version__"]
