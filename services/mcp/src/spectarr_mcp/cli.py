"""Run the Spectarr MCP server over standard input and output."""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .api import SpectarrApiClient
from .server import SpectarrMcpServer


def _enabled(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes", "on"}


def _server() -> SpectarrMcpServer:
    api = SpectarrApiClient(
        base_url=os.getenv("SPECTARR_API_URL", os.getenv("SPECTARR_URL", "http://api:8000")),
        api_key=os.getenv("SPECTARR_API_KEY"),
        worker_token=os.getenv("SPECTARR_WORKER_TOKEN"),
        timeout_seconds=float(os.getenv("SPECTARR_API_TIMEOUT", "30")),
    )
    return SpectarrMcpServer(api, allow_writes=_enabled(os.getenv("SPECTARR_MCP_ALLOW_WRITES")))


def run_stdio(server: SpectarrMcpServer) -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = server.handle(message)
        except Exception as error:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(error)},
            }
        if response is not None:
            print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


def make_http_handler(server: SpectarrMcpServer) -> type[BaseHTTPRequestHandler]:
    """Create a stateless Streamable HTTP handler for the MCP endpoint."""

    class McpHttpHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/mcp":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 10 * 1024 * 1024:
                    raise ValueError("Invalid MCP request size")
                message: dict[str, Any] = json.loads(self.rfile.read(length))
                response = server.handle(message)
                if response is None:
                    self.send_response(202)
                    self.end_headers()
                    return
                body = json.dumps(response, separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as request_error:
                body = json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(request_error)}}
                ).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def do_GET(self) -> None:
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.end_headers()

        def log_message(self, message_format: str, *args: Any) -> None:
            print(f"spectarr-mcp: {message_format % args}", file=sys.stderr)

    return McpHttpHandler


def run_http(server: SpectarrMcpServer) -> int:
    host = os.getenv("SPECTARR_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("SPECTARR_MCP_PORT", "8001"))
    http_server = ThreadingHTTPServer((host, port), make_http_handler(server))
    http_server.serve_forever()
    return 0


def main() -> int:
    server = _server()
    transport = os.getenv("SPECTARR_MCP_TRANSPORT", "stdio").lower()
    if transport == "stdio":
        return run_stdio(server)
    if transport == "http":
        return run_http(server)
    raise ValueError("SPECTARR_MCP_TRANSPORT must be stdio or http")


if __name__ == "__main__":
    raise SystemExit(main())
