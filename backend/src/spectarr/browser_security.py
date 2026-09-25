"""Keep browser requests inside explicitly trusted origins and hostnames."""

from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import Request

from .config import Settings


def browser_request_error(request: Request, settings: Settings) -> str | None:
    try:
        return _request_error(request, settings)
    except ValueError:
        return "Invalid browser origin or request host"


def _request_error(request: Request, settings: Settings) -> str | None:
    if len(request.headers.getlist("host")) != 1:
        return "Invalid request host"
    host = request.url.hostname or ""
    try:
        ip_address(host)
        literal_address = True
    except ValueError:
        literal_address = False
    if not literal_address and host.lower() not in {name.lower() for name in settings.trusted_hosts}:
        return "Untrusted request host"
    origin = request.headers.get("origin")
    own_origin = f"{request.url.scheme}://{request.url.netloc}"
    allowed = {own_origin, *settings.cors_origins}
    if origin is not None:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or origin not in allowed:
            return "Untrusted browser origin"
    elif request.headers.get("sec-fetch-site") == "cross-site":
        return "Cross-site browser requests are blocked"
    return None
