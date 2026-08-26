"""API-backed metadata extraction worker."""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Protocol
from urllib import error, parse, request

from . import __version__
from .models import ExtractionResult, SpectrumObservation
from .providers import ProviderRegistry


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        super().__init__(detail)


class WorkerApi(Protocol):
    def get(self, path: str, query: dict[str, Any] | None = None) -> Any: ...
    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any: ...
    def patch(self, path: str, payload: dict[str, Any]) -> Any: ...


class HttpWorkerApi:
    """Authenticated client for the extraction worker API surface."""

    def __init__(
        self,
        base_url: str,
        worker_id: str,
        service_token: str | None = None,
        worker_token: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id
        self.service_token = service_token
        self.worker_token = worker_token
        self.timeout_seconds = timeout_seconds

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, query=query)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, payload=payload)

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("PATCH", path, payload=payload)

    def _request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            clean = {key: value for key, value in query.items() if value is not None}
            url = f"{url}?{parse.urlencode(clean)}"
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {
            "Accept": "application/json",
            "User-Agent": f"spectarr-extractor/{__version__}",
            "X-Spectarr-Worker-Id": self.worker_id,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.service_token:
            headers["Authorization"] = f"Bearer {self.service_token}"
        if self.worker_token:
            headers["X-Spectarr-Worker-Token"] = self.worker_token
        api_request = request.Request(url, data=body, headers=headers, method=method)
        try:
            with request.urlopen(api_request, timeout=self.timeout_seconds) as response:
                content = response.read()
        except error.HTTPError as api_error:
            detail = api_error.read().decode("utf-8", errors="replace")
            raise ApiError(api_error.code, detail) from api_error
        except error.URLError as api_error:
            raise ApiError(0, f"Spectarr API is unavailable: {api_error.reason}") from api_error
        try:
            return json.loads(content) if content else None
        except json.JSONDecodeError as decode_error:
            raise ApiError(502, "Spectarr API returned invalid JSON") from decode_error


class LeaseHeartbeat:
    """Renew a job lease while a provider streams through an artifact."""

    def __init__(self, api: WorkerApi, job_id: str, interval_seconds: float = 10.0) -> None:
        self.api = api
        self.job_id = job_id
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()
        self.lost_event = threading.Event()
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, name=f"heartbeat-{job_id}", daemon=True)

    def __enter__(self) -> "LeaseHeartbeat":
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop_event.set()
        self.thread.join(timeout=max(1.0, self.interval_seconds + 1.0))

    def ensure_lease(self) -> None:
        if self.lost_event.is_set():
            raise RuntimeError(f"Extraction lease was lost: {self.error}")

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            try:
                self.api.post(f"/api/v1/jobs/{self.job_id}/heartbeat")
            except Exception as heartbeat_error:
                self.error = heartbeat_error
                self.lost_event.set()
                return


class SpectrumCatalogPublisher:
    """Publish spectrum metadata in bounded batches during extraction."""

    proton_mass = 1.007276466621

    def __init__(
        self,
        api: WorkerApi,
        artifact_id: str,
        batch_size: int = 500,
    ) -> None:
        self.api = api
        self.artifact_id = artifact_id
        self.batch_size = max(1, min(batch_size, 1000))
        self.catalog_id: str | None = None
        self.entries: list[dict[str, Any]] = []
        self.spectrum_count = 0

    def begin(self, extractor: str, extractor_version: str) -> None:
        self.entries = []
        self.spectrum_count = 0
        catalog = self.api.post(
            f"/api/v1/artifacts/{self.artifact_id}/spectrum-catalogs",
            {
                "extractor": extractor,
                "extractor_version": extractor_version,
                "schema_version": 1,
            },
        )
        self.catalog_id = str(catalog["id"])

    def add(self, spectrum: SpectrumObservation) -> None:
        if self.catalog_id is None:
            raise RuntimeError("Spectrum catalog publishing has not started")
        isolation_lower = None
        isolation_upper = None
        if spectrum.isolation_target_mz is not None:
            isolation_lower = spectrum.isolation_target_mz - (spectrum.isolation_lower_offset or 0.0)
            isolation_upper = spectrum.isolation_target_mz + (spectrum.isolation_upper_offset or 0.0)
        self.entries.append(
            {
                "ordinal": spectrum.ordinal,
                "ms_level_index": spectrum.ms_level_index,
                "native_id": spectrum.native_id,
                "scan_number": spectrum.scan_number,
                "ms_level": spectrum.ms_level,
                "retention_time_seconds": _finite(spectrum.retention_time_seconds),
                "precursor_mz": _finite(spectrum.precursor_mz),
                "precursor_charge": spectrum.precursor_charge,
                "neutral_mass": self._neutral_mass(spectrum),
                "isolation_lower_mz": _finite(isolation_lower),
                "isolation_upper_mz": _finite(isolation_upper),
                "peak_count": spectrum.peak_count,
                "total_ion_current": _finite(spectrum.tic),
                "base_peak_mz": _finite(spectrum.base_peak_mz),
                "base_peak_intensity": _finite(spectrum.bpc),
                "mz_min": _finite(spectrum.mz_min),
                "mz_max": _finite(spectrum.mz_max),
                "polarity": spectrum.polarity,
                "representation": spectrum.representation,
                "collision_energy": _finite(spectrum.collision_energy),
                "activation_type": spectrum.activation_type,
                "ion_mobility": _finite(spectrum.ion_mobility),
                "ion_mobility_unit": spectrum.ion_mobility_unit,
            }
        )
        self.spectrum_count += 1
        if len(self.entries) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.entries:
            return
        if self.catalog_id is None:
            raise RuntimeError("Spectrum catalog publishing has not started")
        self.api.post(
            f"/api/v1/artifacts/{self.artifact_id}/spectrum-catalogs/{self.catalog_id}/entries",
            {"entries": self.entries},
        )
        self.entries = []

    def complete(self) -> None:
        if self.catalog_id is None:
            raise RuntimeError("Spectrum catalog publishing has not started")
        self.flush()
        self.api.post(
            f"/api/v1/artifacts/{self.artifact_id}/spectrum-catalogs/{self.catalog_id}/complete",
            {"spectrum_count": self.spectrum_count},
        )

    @classmethod
    def _neutral_mass(cls, spectrum: SpectrumObservation) -> float | None:
        if spectrum.precursor_mz is None or not spectrum.precursor_charge:
            return None
        charge = abs(spectrum.precursor_charge)
        polarity = (spectrum.polarity or "").lower()
        negative = spectrum.precursor_charge < 0 or polarity == "negative"
        adjustment = cls.proton_mass * charge
        mass = spectrum.precursor_mz * charge + adjustment if negative else spectrum.precursor_mz * charge - adjustment
        return _finite(mass)


def _finite(value: float | int | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


class MetadataExtractionWorker:
    """Claim extraction jobs, run a provider, and publish a versioned result."""

    def __init__(
        self,
        api: WorkerApi,
        providers: ProviderRegistry,
        local_storage_root: Path,
        heartbeat_seconds: float = 10.0,
    ) -> None:
        self.api = api
        self.providers = providers
        self.local_storage_root = local_storage_root.resolve()
        self.heartbeat_seconds = heartbeat_seconds

    def process_one(self) -> bool:
        jobs = self.api.get(
            "/api/v1/jobs",
            {"state": "queued", "kind": "extract_metadata", "limit": 20},
        )
        job = next((value for value in jobs if value.get("kind") == "extract_metadata"), None)
        if job is None:
            return False
        job_id = str(job["id"])
        try:
            claimed = self.api.post(f"/api/v1/jobs/{job_id}/claim")
        except ApiError as claim_error:
            if claim_error.status == 409:
                return True
            raise
        try:
            artifact_id = str(claimed["input_artifact_id"])
            requested_schema = str(claimed.get("parameters", {}).get("schema_version", "1.0"))
            if requested_schema != "1.0":
                raise ValueError(f"Unsupported extraction schema version: {requested_schema}")
            artifact = self.api.get(f"/api/v1/artifacts/{artifact_id}")
            location = self.api.get(f"/api/v1/artifacts/{artifact_id}/location")
            source = self._resolve_source(location)
            catalog = SpectrumCatalogPublisher(
                self.api,
                artifact_id,
                int(os.getenv("SPECTARR_CATALOG_BATCH_SIZE", "500")),
            )
            with LeaseHeartbeat(self.api, job_id, self.heartbeat_seconds) as heartbeat:
                result = self.providers.extract(
                    source,
                    str(artifact.get("format") or ""),
                    on_spectrum=catalog.add,
                    on_provider_start=catalog.begin,
                )
                heartbeat.ensure_lease()
                self.api.post(
                    f"/api/v1/artifacts/{artifact_id}/extraction-results",
                    self._result_payload(result),
                )
                catalog.complete()
                heartbeat.ensure_lease()
            self.api.patch(f"/api/v1/jobs/{job_id}", {"state": "succeeded", "progress": 1.0})
        except Exception as extraction_error:
            try:
                self.api.patch(
                    f"/api/v1/jobs/{job_id}",
                    {"state": "failed", "error": str(extraction_error)[:10000]},
                )
            except ApiError as update_error:
                if update_error.status != 409:
                    raise
        return True

    def _resolve_source(self, location: dict[str, Any]) -> Path:
        relative_value = location.get("relative_path")
        if not relative_value:
            raise ValueError("Artifact location did not include a storage-relative path")
        candidate = (self.local_storage_root / str(relative_value)).resolve(strict=False)
        if not candidate.is_relative_to(self.local_storage_root):
            raise ValueError("Artifact location escapes the local storage root")
        source = candidate.resolve(strict=True)
        if not source.is_file() and not source.is_dir():
            raise ValueError("Artifact location is not a file or directory bundle")
        return source

    @staticmethod
    def _result_payload(result: ExtractionResult) -> dict[str, Any]:
        spectrum_count = result.qc_summary.get("spectrum_count", 0)
        by_level = result.qc_summary.get("spectra_by_ms_level", {})
        duration_seconds = result.qc_summary.get("acquisition_duration_seconds")
        return {
            "schema_version": result.schema_version,
            "extractor": result.parser_provider,
            "extractor_version": result.parser_version,
            "result_type": "metadata",
            "payload": {
                "source_format": result.source_format,
                "metadata": result.metadata,
                "qc_summary": result.qc_summary,
                "spectrum_count": spectrum_count,
                "ms2_count": by_level.get("2", 0),
                "duration_minutes": duration_seconds / 60.0 if duration_seconds is not None else None,
            },
            "warnings": result.warnings,
        }

    def run_forever(self, poll_seconds: float = 3.0) -> None:
        failures = 0
        while True:
            try:
                handled = self.process_one()
                failures = 0
            except Exception as worker_error:
                failures += 1
                print(f"Metadata worker API error: {worker_error}", file=sys.stderr)
                time.sleep(min(30.0, poll_seconds * (2 ** min(failures, 4))))
                continue
            if not handled:
                time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Spectarr metadata extraction worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    args = parser.parse_args()
    worker_id = os.getenv("SPECTARR_WORKER_ID", f"extractor-{socket.gethostname()}")
    api = HttpWorkerApi(
        os.getenv("SPECTARR_API_URL", "http://api:8000"),
        worker_id,
        os.getenv("SPECTARR_SERVICE_TOKEN") or os.getenv("SPECTARR_API_KEY"),
        os.getenv("SPECTARR_WORKER_TOKEN"),
        float(os.getenv("SPECTARR_API_TIMEOUT", "60")),
    )
    worker = MetadataExtractionWorker(
        api,
        ProviderRegistry(),
        Path(os.getenv("SPECTARR_LOCAL_STORAGE_ROOT", "/data/storage")),
        float(os.getenv("SPECTARR_HEARTBEAT_SECONDS", "10")),
    )
    if args.once:
        worker.process_one()
    else:
        worker.run_forever(max(0.1, min(args.poll_seconds, 60.0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
