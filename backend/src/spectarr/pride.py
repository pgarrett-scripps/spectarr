"""Public PRIDE discovery with a deliberately narrow download destination policy."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

BASE = "https://www.ebi.ac.uk/pride/ws/archive/v3"
MAX_METADATA_BYTES = 20 * 1024 * 1024
SUPPORTED = (".raw", ".mzml", ".mzml.gz", ".mzxml", ".mgf", ".mgf.gz", ".ms2", ".ms2.gz")


def accession_from_input(value: str) -> str:
    value = value.strip()
    if value.startswith("https://"):
        url = urlsplit(value)
        if url.hostname != "www.ebi.ac.uk" or url.query or url.fragment:
            raise ValueError("Enter a PRIDE project URL or PXD accession")
        match = re.fullmatch(r"/pride/archive/projects/(PXD\d{6,9})/?", url.path, re.I)
        value = match[1] if match else ""
    if not re.fullmatch(r"PXD\d{6,9}", value, re.I):
        raise ValueError("Enter a PRIDE PXD accession, for example PXD000001")
    return value.upper()


def download_url(value: str) -> str:
    url = urlsplit(value)
    path = unquote(url.path)
    if (url.scheme not in {"ftp", "https"} or url.hostname != "ftp.pride.ebi.ac.uk"
            or url.port not in {None, 443} or url.username or url.password or url.query or url.fragment
            or not path.startswith("/pride/data/archive/") or ".." in path.split("/")
            or "\\" in path or any(ord(c) < 32 for c in path)):
        raise ValueError("File has no supported PRIDE HTTPS download location")
    return urlunsplit(("https", "ftp.pride.ebi.ac.uk", url.path, "", ""))


def check_public_host(host: str):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
        raise ValueError("Repository host must resolve to public addresses")


def client():
    # Fixed provider hosts, no redirects and no environment proxy inheritance.
    return httpx.Client(timeout=httpx.Timeout(20, connect=10), follow_redirects=False, trust_env=False)


def metadata(http: httpx.Client, path: str):
    with http.stream("GET", BASE + path, headers={"Accept": "application/json"}) as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_bytes(65536):
            content.extend(chunk)
            if len(content) > MAX_METADATA_BYTES:
                raise ValueError("Repository listing is too large. Choose a smaller dataset")
        return json.loads(content)


def checksum(value: str) -> dict | None:
    value = (value or "").strip().lower()
    parts = value.split(":", 1)
    digest = parts[-1]
    algorithm = {32: "md5", 40: "sha1", 64: "sha256"}.get(len(digest))
    if not algorithm or not re.fullmatch(r"[0-9a-f]+", digest):
        return None
    if len(parts) == 2 and parts[0].replace("-", "") != algorithm:
        return None
    return {"algorithm": algorithm, "value": digest}


def lookup(value: str) -> dict:
    accession = accession_from_input(value)
    check_public_host("www.ebi.ac.uk")
    with client() as http:
        project = metadata(http, f"/projects/{accession}")
        records = metadata(http, f"/projects/{accession}/files/all")
        sdrf_warning = None
        try:
            sdrf_locations = metadata(http, f"/files/sdrf/{accession}")
        except (httpx.HTTPError, ValueError, OSError):
            sdrf_locations = []
            sdrf_warning = "PRIDE SDRF discovery is unavailable. Acquisitions can still be imported"
    if not isinstance(project, dict) or project.get("accession") != accession or not isinstance(records, list):
        raise ValueError("Unexpected PRIDE response")
    if isinstance(sdrf_locations, list):
        known_urls = {location.get("value") for record in records for location in record.get("publicFileLocations", [])}
        for location in sdrf_locations:
            if isinstance(location, str) and location not in known_urls:
                records.append({"fileName": unquote(urlsplit(location).path).rsplit("/", 1)[-1],
                                "publicFileLocations": [{"value": location}]})
    files = []
    for record in records:
        name = str(record.get("fileName", ""))
        locations = record.get("publicFileLocations") or []
        url = None
        for location in locations:
            try:
                url = download_url(location.get("value", ""))
                break
            except ValueError:
                pass
        size = record.get("fileSizeBytes")
        supported = bool(url and isinstance(size, int) and size > 0 and name.lower().endswith(SUPPORTED)
                         and name not in {".", ".."} and not any(c in name for c in "/\\\x00"))
        files.append({
            "id": str(record.get("accession") or hashlib.sha256((url or name).encode()).hexdigest()),
            "filename": name, "byte_size": size if isinstance(size, int) and size >= 0 else 0,
            "category": (record.get("fileCategory") or {}).get("value", "OTHER"),
            "url": url, "checksum": checksum(record.get("checksum")), "supported": supported,
            "is_sdrf": bool(url and re.search(r"(?:^|[._-])sdrf\.tsv$", name, re.I)),
            "reason": None if supported else "Requires a supported single acquisition file and PRIDE HTTPS location",
        })
    return {
        "accession": accession, "title": project.get("title", accession),
        "description": project.get("projectDescription", ""), "doi": project.get("doi"),
        "license": project.get("license"),
        "url": f"https://www.ebi.ac.uk/pride/archive/projects/{accession}", "files": files,
        "sdrf_warning": sdrf_warning,
    }
