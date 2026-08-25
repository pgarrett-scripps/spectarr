"""Streaming Mascot Generic Format metadata parser."""

from __future__ import annotations

from pathlib import Path

from ..models import ExtractionResult, SpectrumObservation, SummaryBuilder
from .base import ProviderError, normalized_format


class MgfProvider:
    name = "spectarr-mgf"
    version = "1"
    optional = False

    def supports(self, path: Path, declared_format: str | None = None) -> bool:
        return normalized_format(path, declared_format) == "MGF"

    def extract(self, path: Path, declared_format: str | None = None) -> ExtractionResult:
        builder = SummaryBuilder()
        metadata: dict[str, str] = {}
        current: dict[str, object] | None = None
        malformed_lines = 0
        try:
            with path.open("rt", encoding="utf-8", errors="replace") as stream:
                for raw_line in stream:
                    line = raw_line.strip()
                    if not line:
                        continue
                    upper = line.upper()
                    if upper == "BEGIN IONS":
                        if current is not None:
                            malformed_lines += 1
                        current = {"peak_count": 0, "tic": 0.0, "bpc": None, "mz_min": None, "mz_max": None}
                        continue
                    if upper == "END IONS":
                        if current is not None:
                            builder.add(self._observation(current))
                        current = None
                        continue
                    if current is None:
                        if "=" in line:
                            key, value = line.split("=", 1)
                            if len(metadata) < 100:
                                metadata[key.upper()] = value
                        continue
                    if "=" in line:
                        key, value = line.split("=", 1)
                        current[key.upper()] = value
                        continue
                    pieces = line.split()
                    if len(pieces) < 2:
                        malformed_lines += 1
                        continue
                    try:
                        mz_value = float(pieces[0])
                        intensity = float(pieces[1])
                    except ValueError:
                        malformed_lines += 1
                        continue
                    current["peak_count"] = int(current["peak_count"]) + 1
                    current["tic"] = float(current["tic"]) + intensity
                    current["bpc"] = intensity if current["bpc"] is None else max(float(current["bpc"]), intensity)
                    current["mz_min"] = (
                        mz_value if current["mz_min"] is None else min(float(current["mz_min"]), mz_value)
                    )
                    current["mz_max"] = (
                        mz_value if current["mz_max"] is None else max(float(current["mz_max"]), mz_value)
                    )
        except OSError as error:
            raise ProviderError(f"Could not read MGF: {error}") from error
        if current is not None:
            builder.add(self._observation(current))
            builder.warnings.append("The final MGF spectrum did not contain END IONS")
        if malformed_lines:
            builder.warnings.append(f"Ignored {malformed_lines} malformed MGF lines")
        summary, warnings = builder.finish()
        return ExtractionResult(self.name, self.version, "MGF", {"headers": metadata}, summary, warnings)

    @staticmethod
    def _observation(values: dict[str, object]) -> SpectrumObservation:
        precursor = _first_float(values.get("PEPMASS"))
        charge = _charge(values.get("CHARGE"))
        rt = _first_float(values.get("RTINSECONDS"))
        return SpectrumObservation(
            ms_level=2,
            retention_time_seconds=rt,
            representation="centroid",
            peak_count=int(values["peak_count"]),
            mz_min=_optional_float(values.get("mz_min")),
            mz_max=_optional_float(values.get("mz_max")),
            tic=_optional_float(values.get("tic")),
            bpc=_optional_float(values.get("bpc")),
            precursor_mz=precursor,
            precursor_charge=charge,
        )


def _first_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", " ").split()[0])
    except (ValueError, IndexError):
        return None


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None


def _charge(value: object) -> int | None:
    if value is None:
        return None
    token = str(value).replace("and", " ").replace(",", " ").split()[0].rstrip("+-")
    try:
        return int(token)
    except ValueError:
        return None
