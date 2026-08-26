"""Streaming MS2 metadata parser."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..models import ExtractionResult, SpectrumObservation, SummaryBuilder
from .base import ProviderError, normalized_format


class Ms2Provider:
    name = "spectarr-ms2"
    version = "1"
    optional = False

    def supports(self, path: Path, declared_format: str | None = None) -> bool:
        return normalized_format(path, declared_format) == "MS2"

    def extract(
        self,
        path: Path,
        declared_format: str | None = None,
        on_spectrum: Callable[[SpectrumObservation], None] | None = None,
    ) -> ExtractionResult:
        builder = SummaryBuilder(on_spectrum=on_spectrum)
        headers: dict[str, str] = {}
        current: dict[str, object] | None = None
        malformed = 0
        try:
            with path.open("rt", encoding="utf-8", errors="replace") as stream:
                for raw_line in stream:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    record = parts[0]
                    if record == "H" and len(parts) >= 3:
                        if len(headers) < 100:
                            headers[parts[1]] = " ".join(parts[2:])
                    elif record == "S" and len(parts) >= 4:
                        if current is not None:
                            builder.add(self._observation(current))
                        current = self._new_spectrum(parts)
                    elif record == "I" and current is not None and len(parts) >= 3:
                        current[parts[1]] = " ".join(parts[2:])
                    elif record == "Z" and current is not None and len(parts) >= 2:
                        try:
                            current["charge"] = int(parts[1])
                        except ValueError:
                            malformed += 1
                    elif current is not None and len(parts) >= 2:
                        try:
                            mz_value = float(parts[0])
                            intensity = float(parts[1])
                        except ValueError:
                            malformed += 1
                            continue
                        current["peak_count"] = int(current["peak_count"]) + 1
                        current["tic"] = float(current["tic"]) + intensity
                        if current["bpc"] is None or intensity > float(current["bpc"]):
                            current["bpc"] = intensity
                            current["base_peak_mz"] = mz_value
                        current["mz_min"] = (
                            mz_value if current["mz_min"] is None else min(float(current["mz_min"]), mz_value)
                        )
                        current["mz_max"] = (
                            mz_value if current["mz_max"] is None else max(float(current["mz_max"]), mz_value)
                        )
        except OSError as error:
            raise ProviderError(f"Could not read MS2: {error}") from error
        if current is not None:
            builder.add(self._observation(current))
        if malformed:
            builder.warnings.append(f"Ignored {malformed} malformed MS2 records")
        summary, warnings = builder.finish()
        return ExtractionResult(self.name, self.version, "MS2", {"headers": headers}, summary, warnings)

    @staticmethod
    def _new_spectrum(parts: list[str]) -> dict[str, object]:
        return {
            "scan_number": _int(parts[1]),
            "precursor_mz": _float(parts[3]),
            "peak_count": 0,
            "tic": 0.0,
            "bpc": None,
            "base_peak_mz": None,
            "mz_min": None,
            "mz_max": None,
        }

    @staticmethod
    def _observation(values: dict[str, object]) -> SpectrumObservation:
        rt = _float(values.get("RetTime") or values.get("RTime") or values.get("RetentionTime"))
        return SpectrumObservation(
            native_id=f"scan={values['scan_number']}" if values.get("scan_number") is not None else None,
            scan_number=int(values["scan_number"]) if values.get("scan_number") is not None else None,
            ms_level=2,
            retention_time_seconds=rt,
            representation="centroid",
            peak_count=int(values["peak_count"]),
            mz_min=_float(values.get("mz_min")),
            mz_max=_float(values.get("mz_max")),
            tic=_float(values.get("tic")),
            bpc=_float(values.get("bpc")),
            base_peak_mz=_float(values.get("base_peak_mz")),
            precursor_mz=_float(values.get("precursor_mz")),
            precursor_charge=int(values["charge"]) if "charge" in values else None,
        )


def _float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
