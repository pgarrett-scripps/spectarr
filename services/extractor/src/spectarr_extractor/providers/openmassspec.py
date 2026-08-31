"""Optional OpenMassSpec provider with runtime feature detection."""

from __future__ import annotations

import importlib
import importlib.util
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

from ..models import ExtractionResult, SpectrumObservation, SummaryBuilder
from .base import ProviderError, ProviderUnavailable, normalized_format


class OpenMassSpecProvider:
    name = "openmassspec"
    optional = True

    @property
    def version(self) -> str:
        for distribution in ("openmassspec", "openmassspec-io"):
            try:
                return metadata.version(distribution)
            except metadata.PackageNotFoundError:
                continue
        return "unknown"

    def is_available(self) -> bool:
        for module_name in ("openmassspec", "openmassspec_io"):
            try:
                if importlib.util.find_spec(module_name) is not None:
                    return True
            except (ImportError, ValueError):
                continue
        return False

    def supports(self, path: Path, declared_format: str | None = None) -> bool:
        if not self.is_available():
            return False
        source_format = normalized_format(path, declared_format).lower()
        supported = {
            "raw",
            "wiff",
            "wiff2",
            "d",
            "tdf",
            "vendor_directory",
        }
        return source_format in supported

    def extract(
        self,
        path: Path,
        declared_format: str | None = None,
        on_spectrum: Callable[[SpectrumObservation], None] | None = None,
    ) -> ExtractionResult:
        module = self._module()
        builder = SummaryBuilder(on_spectrum=on_spectrum)
        try:
            iterator = module.iter_spectra(str(path))
            for spectrum in iterator:
                mz_values = _attribute(spectrum, "mz")
                intensity_values = _attribute(spectrum, "intensity")
                mobility_values = _attribute(spectrum, "inv_mobility_per_peak")
                peak_count, mz_min, mz_max = _mz_stats(mz_values)
                tic, bpc = _intensity_stats(intensity_values)
                mobility = _first_number(
                    _attribute(spectrum, "ion_mobility", "inverse_mobility", "inv_mobility")
                )
                mobility_count, mobility_min, mobility_max = _mz_stats(mobility_values)
                builder.add(
                    SpectrumObservation(
                        native_id=_string(_attribute(spectrum, "native_id", "id")),
                        scan_number=_integer(_attribute(spectrum, "scan_number", "scan")),
                        ms_level=int(_attribute(spectrum, "ms_level") or 1),
                        retention_time_seconds=_number(_attribute(spectrum, "retention_time_sec")),
                        polarity=_polarity(_attribute(spectrum, "polarity")),
                        representation=_representation(_attribute(spectrum, "scan_mode")),
                        peak_count=peak_count,
                        mz_min=mz_min,
                        mz_max=mz_max,
                        tic=tic,
                        bpc=bpc,
                        base_peak_mz=_base_peak_mz(mz_values, intensity_values),
                        precursor_mz=_number(_attribute(spectrum, "precursor_mz", "selected_ion_mz")),
                        precursor_charge=_integer(_attribute(spectrum, "precursor_charge", "charge")),
                        collision_energy=_number(_attribute(spectrum, "collision_energy", "collision_energy_ev")),
                        activation_type=_string(_attribute(spectrum, "activation_type", "activation")),
                        ion_mobility=mobility,
                        ion_mobility_min=mobility_min,
                        ion_mobility_max=mobility_max,
                        ion_mobility_unit=_string(_attribute(spectrum, "ion_mobility_unit")),
                        ion_mobility_present=mobility is not None or bool(mobility_count),
                        isolation_target_mz=_number(_attribute(spectrum, "isolation_target_mz")),
                        isolation_lower_offset=_number(_attribute(spectrum, "isolation_lower_offset")),
                        isolation_upper_offset=_number(_attribute(spectrum, "isolation_upper_offset")),
                        dia="dia" in (_string(_attribute(spectrum, "acquisition_mode")) or "").lower(),
                    )
                )
        except Exception as error:
            raise ProviderError(f"OpenMassSpec could not read the artifact: {error}") from error
        summary, warnings = builder.finish()
        source_format = normalized_format(path, declared_format)
        return ExtractionResult(self.name, self.version, source_format, {}, summary, warnings)

    @staticmethod
    def _module():
        failures = []
        for module_name in ("openmassspec", "openmassspec_io"):
            try:
                return importlib.import_module(module_name)
            except (ImportError, OSError) as error:
                failures.append(str(error))
                continue
        detail = ", ".join(failures) or "package not found"
        raise ProviderUnavailable(f"Install the openmassspec extra to enable this provider: {detail}")


def _attribute(value: object, *names: str) -> Any:
    for name in names:
        try:
            result = getattr(value, name)
        except (AttributeError, RuntimeError):
            continue
        if result is not None:
            return result
    return None


def _mz_stats(values: Any) -> tuple[int | None, float | None, float | None]:
    if values is None:
        return None, None, None
    try:
        count = len(values)
        return count, float(values.min()) if count else None, float(values.max()) if count else None
    except (AttributeError, TypeError, ValueError):
        count = 0
        minimum = None
        maximum = None
        for value in values:
            number = float(value)
            count += 1
            minimum = number if minimum is None else min(minimum, number)
            maximum = number if maximum is None else max(maximum, number)
        return count, minimum, maximum


def _intensity_stats(values: Any) -> tuple[float | None, float | None]:
    if values is None:
        return None, None
    try:
        count = len(values)
        return float(values.sum()) if count else 0.0, float(values.max()) if count else None
    except (AttributeError, TypeError, ValueError):
        total = 0.0
        maximum = None
        for value in values:
            number = float(value)
            total += number
            maximum = number if maximum is None else max(maximum, number)
        return total, maximum


def _base_peak_mz(mz_values: Any, intensity_values: Any) -> float | None:
    if mz_values is None or intensity_values is None:
        return None
    try:
        if len(intensity_values) == 0:
            return None
        return float(mz_values[intensity_values.argmax()])
    except (AttributeError, TypeError, ValueError, IndexError):
        best_mz = None
        best_intensity = None
        for mz_value, intensity in zip(mz_values, intensity_values):
            number = float(intensity)
            if best_intensity is None or number > best_intensity:
                best_intensity = number
                best_mz = float(mz_value)
        return best_mz


def _first_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            return float(value[0]) if len(value) else None
        return float(value)
    except (TypeError, ValueError, IndexError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _polarity(value: Any) -> str | None:
    text = (_string(value) or "").lower()
    if "pos" in text or text == "+":
        return "positive"
    if "neg" in text or text == "-":
        return "negative"
    return text or None


def _representation(value: Any) -> str | None:
    text = (_string(value) or "").lower()
    if "centroid" in text:
        return "centroid"
    if "profile" in text:
        return "profile"
    return text or None
