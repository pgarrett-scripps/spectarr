"""Versioned metadata extraction models and bounded summary aggregation."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


SCHEMA_VERSION = "1.0"


@dataclass
class SpectrumObservation:
    """Normalized metadata for one spectrum without retaining peak arrays."""

    ordinal: int | None = None
    ms_level_index: int | None = None
    native_id: str | None = None
    scan_number: int | None = None
    ms_level: int = 1
    retention_time_seconds: float | None = None
    polarity: str | None = None
    representation: str | None = None
    peak_count: int | None = None
    mz_min: float | None = None
    mz_max: float | None = None
    tic: float | None = None
    bpc: float | None = None
    base_peak_mz: float | None = None
    precursor_mz: float | None = None
    precursor_charge: int | None = None
    collision_energy: float | None = None
    activation_type: str | None = None
    ion_mobility: float | None = None
    ion_mobility_min: float | None = None
    ion_mobility_max: float | None = None
    ion_mobility_unit: str | None = None
    ion_mobility_present: bool = False
    isolation_target_mz: float | None = None
    isolation_lower_offset: float | None = None
    isolation_upper_offset: float | None = None
    dia: bool = False


@dataclass
class RunningStats:
    """Constant-memory numeric summary."""

    count: int = 0
    minimum: float | None = None
    maximum: float | None = None
    total: float = 0.0

    def add(self, value: float | int | None) -> None:
        if value is None:
            return
        number = float(value)
        if not math.isfinite(number):
            return
        self.count += 1
        self.total += number
        self.minimum = number if self.minimum is None else min(self.minimum, number)
        self.maximum = number if self.maximum is None else max(self.maximum, number)

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "count": self.count,
            "min": self.minimum,
            "max": self.maximum,
            "mean": self.total / self.count if self.count else None,
        }


class BoundedSeries:
    """Preserve an ordered preview while bounding memory usage."""

    def __init__(self, maximum_points: int = 1000) -> None:
        self.maximum_points = maximum_points
        self.points: list[dict[str, float]] = []

    def add(self, retention_time: float | None, value: float | None) -> None:
        if retention_time is None or value is None:
            return
        if not math.isfinite(float(retention_time)) or not math.isfinite(float(value)):
            return
        self.points.append({"retention_time_seconds": float(retention_time), "value": float(value)})
        if len(self.points) >= self.maximum_points * 2:
            final_point = self.points[-1]
            self.points = self.points[::2]
            if self.points[-1] != final_point:
                self.points[-1] = final_point

    def finish(self) -> list[dict[str, float]]:
        ordered = sorted(self.points, key=lambda point: point["retention_time_seconds"])
        if len(ordered) <= self.maximum_points:
            return ordered
        stride = max(1, len(ordered) // self.maximum_points)
        result = ordered[::stride][: self.maximum_points]
        if result and result[-1] != ordered[-1]:
            result[-1] = ordered[-1]
        return result


@dataclass
class ExtractionResult:
    """Portable extraction payload submitted to the Spectarr API."""

    parser_provider: str
    parser_version: str
    source_format: str
    metadata: dict[str, Any]
    qc_summary: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SummaryBuilder:
    """Accumulate run metadata from a stream of normalized spectra."""

    def __init__(
        self,
        preview_points: int = 1000,
        dia_window_limit: int = 1000,
        on_spectrum: Callable[[SpectrumObservation], None] | None = None,
    ) -> None:
        self.spectrum_count = 0
        self.ms_levels: Counter[int] = Counter()
        self.polarities: set[str] = set()
        self.representations: set[str] = set()
        self.retention_times = RunningStats()
        self.mz_range = RunningStats()
        self.peak_counts = RunningStats()
        self.tic_stats = RunningStats()
        self.bpc_stats = RunningStats()
        self.precursor_mz = RunningStats()
        self.precursor_charges: Counter[int] = Counter()
        self.collision_energy = RunningStats()
        self.ion_mobility = RunningStats()
        self.ion_mobility_units: set[str] = set()
        self.ion_mobility_spectrum_count = 0
        self.tic_preview = BoundedSeries(preview_points)
        self.bpc_preview = BoundedSeries(preview_points)
        self.dia_window_limit = dia_window_limit
        self.dia_windows: set[tuple[float, float, float]] = set()
        self.dia_detected = False
        self.dia_windows_truncated = False
        self.warnings: list[str] = []
        self.on_spectrum = on_spectrum

    def add(self, spectrum: SpectrumObservation) -> None:
        spectrum.ordinal = self.spectrum_count
        spectrum.ms_level_index = self.ms_levels[spectrum.ms_level]
        self.spectrum_count += 1
        self.ms_levels[spectrum.ms_level] += 1
        if spectrum.polarity:
            self.polarities.add(spectrum.polarity)
        if spectrum.representation:
            self.representations.add(spectrum.representation)
        self.retention_times.add(spectrum.retention_time_seconds)
        self.mz_range.add(spectrum.mz_min)
        self.mz_range.add(spectrum.mz_max)
        self.peak_counts.add(spectrum.peak_count)
        self.tic_stats.add(spectrum.tic)
        self.bpc_stats.add(spectrum.bpc)
        self.precursor_mz.add(spectrum.precursor_mz)
        if spectrum.precursor_charge is not None:
            self.precursor_charges[spectrum.precursor_charge] += 1
        self.collision_energy.add(spectrum.collision_energy)
        self.tic_preview.add(spectrum.retention_time_seconds, spectrum.tic)
        self.bpc_preview.add(spectrum.retention_time_seconds, spectrum.bpc)
        if spectrum.ion_mobility is not None:
            self.ion_mobility.add(spectrum.ion_mobility)
        self.ion_mobility.add(spectrum.ion_mobility_min)
        self.ion_mobility.add(spectrum.ion_mobility_max)
        if spectrum.ion_mobility_present or spectrum.ion_mobility is not None:
            self.ion_mobility_spectrum_count += 1
        if spectrum.ion_mobility_unit:
            self.ion_mobility_units.add(spectrum.ion_mobility_unit)
        window = self._window(spectrum)
        self.dia_detected = self.dia_detected or spectrum.dia
        if window:
            if len(self.dia_windows) < self.dia_window_limit:
                self.dia_windows.add(window)
            elif window not in self.dia_windows:
                self.dia_windows_truncated = True
        if self.on_spectrum:
            self.on_spectrum(spectrum)

    def finish(self) -> tuple[dict[str, Any], list[str]]:
        if self.spectrum_count == 0:
            self.warnings.append("No spectra were found")
        if self.retention_times.count == 0:
            self.warnings.append("Retention times were not available")
        if self.tic_stats.count == 0:
            self.warnings.append("TIC values could not be derived")
        if self.ion_mobility_spectrum_count and self.ion_mobility.count == 0:
            self.warnings.append("Ion mobility data were present but their numeric range was unavailable")
        duration = None
        if self.retention_times.minimum is not None and self.retention_times.maximum is not None:
            duration = max(0.0, self.retention_times.maximum - self.retention_times.minimum)
        windows = [
            {"target_mz": target, "lower_mz": lower, "upper_mz": upper}
            for target, lower, upper in sorted(self.dia_windows)
        ]
        summary = {
            "spectrum_count": self.spectrum_count,
            "spectra_by_ms_level": {str(level): count for level, count in sorted(self.ms_levels.items())},
            "polarities": sorted(self.polarities),
            "representations": sorted(self.representations),
            "retention_time_seconds": self.retention_times.to_dict(),
            "acquisition_duration_seconds": duration,
            "mz_range": {"min": self.mz_range.minimum, "max": self.mz_range.maximum},
            "peak_count": self.peak_counts.to_dict(),
            "tic": self.tic_stats.to_dict(),
            "bpc": self.bpc_stats.to_dict(),
            "precursors": {
                "count": self.precursor_mz.count,
                "mz": self.precursor_mz.to_dict(),
                "charge_counts": {str(charge): count for charge, count in sorted(self.precursor_charges.items())},
                "collision_energy": self.collision_energy.to_dict(),
            },
            "chromatogram_preview": {
                "maximum_points": self.tic_preview.maximum_points,
                "tic": self.tic_preview.finish(),
                "bpc": self.bpc_preview.finish(),
            },
            "ion_mobility": {
                "present": self.ion_mobility_spectrum_count > 0,
                "spectrum_count": self.ion_mobility_spectrum_count,
                "range": self.ion_mobility.to_dict(),
                "units": sorted(self.ion_mobility_units),
            },
            "dia": {
                "detected": self.dia_detected,
                "window_count": len(self.dia_windows),
                "windows_truncated": self.dia_windows_truncated,
                "windows": windows,
            },
        }
        if self.dia_windows_truncated:
            self.warnings.append("Isolation window details reached the configured storage limit")
        return summary, self.warnings

    @staticmethod
    def _window(spectrum: SpectrumObservation) -> tuple[float, float, float] | None:
        if spectrum.isolation_target_mz is None:
            return None
        lower = spectrum.isolation_target_mz - (spectrum.isolation_lower_offset or 0.0)
        upper = spectrum.isolation_target_mz + (spectrum.isolation_upper_offset or 0.0)
        if not all(math.isfinite(value) for value in (spectrum.isolation_target_mz, lower, upper)):
            return None
        return (spectrum.isolation_target_mz, lower, upper)
