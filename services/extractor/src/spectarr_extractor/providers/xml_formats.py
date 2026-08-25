"""Streaming mzML and mzXML metadata providers."""

from __future__ import annotations

import gzip
import math
import re
from pathlib import Path
from typing import BinaryIO
from xml.parsers import expat

from ..models import ExtractionResult, SpectrumObservation, SummaryBuilder
from .base import ProviderError, normalized_format


MZML_VALUES = {
    "MS:1000511": "ms_level",
    "MS:1000016": "retention_time",
    "MS:1000130": "positive",
    "MS:1000129": "negative",
    "MS:1000127": "centroid",
    "MS:1000128": "profile",
    "MS:1000528": "mz_min",
    "MS:1000527": "mz_max",
    "MS:1000285": "tic",
    "MS:1000505": "bpc",
    "MS:1000744": "precursor_mz",
    "MS:1000041": "precursor_charge",
    "MS:1000045": "collision_energy",
    "MS:1002476": "ion_mobility",
    "MS:1002815": "ion_mobility",
    "MS:1003008": "ion_mobility",
    "MS:1000827": "isolation_target_mz",
    "MS:1000828": "isolation_lower_offset",
    "MS:1000829": "isolation_upper_offset",
}


class MzmlProvider:
    name = "spectarr-mzml"
    version = "1"
    optional = False

    def supports(self, path: Path, declared_format: str | None = None) -> bool:
        return normalized_format(path, declared_format) == "mzML"

    def extract(self, path: Path, declared_format: str | None = None) -> ExtractionResult:
        builder = SummaryBuilder()
        handler = _MzmlHandler(builder)
        _parse_xml(path, handler.start, handler.end, None)
        summary, warnings = builder.finish()
        if handler.declared_spectrum_count is not None and handler.declared_spectrum_count != summary["spectrum_count"]:
            warnings.append("Declared mzML spectrum count did not match parsed spectra")
        if summary["mz_range"]["min"] is None:
            warnings.append("Observed m/z range terms were not present in the mzML")
        metadata = {
            "run_id": handler.run_id,
            "declared_spectrum_count": handler.declared_spectrum_count,
            "instrument_models": sorted(handler.instrument_models),
        }
        return ExtractionResult(self.name, self.version, "mzML", metadata, summary, warnings)


class _MzmlHandler:
    def __init__(self, builder: SummaryBuilder) -> None:
        self.builder = builder
        self.current: SpectrumObservation | None = None
        self.run_id: str | None = None
        self.declared_spectrum_count: int | None = None
        self.instrument_models: set[str] = set()
        self.run_is_dia = False

    def start(self, name: str, attributes: dict[str, str]) -> None:
        element = _local(name)
        attrs = _attrs(attributes)
        if element == "run":
            self.run_id = attrs.get("id")
        elif element == "spectrumList":
            self.declared_spectrum_count = _integer(attrs.get("count"))
        elif element == "spectrum":
            self.current = SpectrumObservation(
                peak_count=_integer(attrs.get("defaultArrayLength")),
                dia=self.run_is_dia,
            )
        elif element == "cvParam":
            self._cv_param(attrs)

    def end(self, name: str) -> None:
        if _local(name) == "spectrum" and self.current is not None:
            self.builder.add(self.current)
            self.current = None

    def _cv_param(self, attrs: dict[str, str]) -> None:
        accession = attrs.get("accession", "")
        value = attrs.get("value")
        lowered_name = attrs.get("name", "").lower()
        if "data independent acquisition" in lowered_name or "swath" in lowered_name:
            self.run_is_dia = True
            if self.current is not None:
                self.current.dia = True
        if self.current is None:
            if accession.startswith("MS:100") and "instrument model" in attrs.get("name", "").lower():
                self.instrument_models.add(attrs.get("name", accession))
            return
        field_name = MZML_VALUES.get(accession)
        if "ion mobility array" in lowered_name or "drift time array" in lowered_name:
            self.current.ion_mobility_present = True
        if field_name == "positive":
            self.current.polarity = "positive"
        elif field_name == "negative":
            self.current.polarity = "negative"
        elif field_name in {"centroid", "profile"}:
            self.current.representation = field_name
        elif field_name == "ms_level":
            self.current.ms_level = _integer(value) or 1
        elif field_name == "precursor_charge":
            self.current.precursor_charge = _integer(value)
        elif field_name == "retention_time":
            seconds = _number(value)
            if seconds is not None and attrs.get("unitName", "").lower().startswith("min"):
                seconds *= 60.0
            self.current.retention_time_seconds = seconds
        elif field_name:
            setattr(self.current, field_name, _number(value))
            if field_name == "ion_mobility":
                self.current.ion_mobility_unit = attrs.get("unitName") or attrs.get("unitAccession")


class MzxmlProvider:
    name = "spectarr-mzxml"
    version = "1"
    optional = False

    def supports(self, path: Path, declared_format: str | None = None) -> bool:
        return normalized_format(path, declared_format) == "mzXML"

    def extract(self, path: Path, declared_format: str | None = None) -> ExtractionResult:
        builder = SummaryBuilder()
        handler = _MzxmlHandler(builder)
        _parse_xml(path, handler.start, handler.end, handler.text)
        summary, warnings = builder.finish()
        if handler.declared_spectrum_count is not None and handler.declared_spectrum_count != summary["spectrum_count"]:
            warnings.append("Declared mzXML spectrum count did not match parsed spectra")
        metadata = {"declared_spectrum_count": handler.declared_spectrum_count}
        return ExtractionResult(self.name, self.version, "mzXML", metadata, summary, warnings)


class _MzxmlHandler:
    def __init__(self, builder: SummaryBuilder) -> None:
        self.builder = builder
        self.scans: list[SpectrumObservation] = []
        self.precursor_text: list[str] | None = None
        self.precursor_attributes: dict[str, str] = {}
        self.declared_spectrum_count: int | None = None

    def start(self, name: str, attributes: dict[str, str]) -> None:
        element = _local(name)
        attrs = _attrs(attributes)
        if element == "msRun":
            self.declared_spectrum_count = _integer(attrs.get("scanCount"))
        elif element == "scan":
            self.scans.append(
                SpectrumObservation(
                    ms_level=_integer(attrs.get("msLevel")) or 1,
                    retention_time_seconds=_duration_seconds(attrs.get("retentionTime")),
                    polarity={"+": "positive", "-": "negative"}.get(attrs.get("polarity", "")),
                    representation=(
                        "centroid"
                        if attrs.get("centroided") == "1"
                        else "profile"
                        if attrs.get("centroided") == "0"
                        else None
                    ),
                    peak_count=_integer(attrs.get("peaksCount")),
                    mz_min=_number(attrs.get("lowMz")),
                    mz_max=_number(attrs.get("highMz")),
                    tic=_number(attrs.get("totIonCurrent")),
                    bpc=_number(attrs.get("basePeakIntensity")),
                    collision_energy=_number(attrs.get("collisionEnergy")),
                )
            )
        elif element == "precursorMz" and self.scans:
            self.precursor_text = []
            self.precursor_attributes = attrs

    def text(self, value: str) -> None:
        if self.precursor_text is not None:
            self.precursor_text.append(value)

    def end(self, name: str) -> None:
        element = _local(name)
        if element == "precursorMz" and self.scans:
            scan = self.scans[-1]
            scan.precursor_mz = _number("".join(self.precursor_text or []))
            scan.precursor_charge = _integer(self.precursor_attributes.get("precursorCharge"))
            scan.collision_energy = scan.collision_energy or _number(self.precursor_attributes.get("collisionEnergy"))
            width = _number(self.precursor_attributes.get("windowWideness"))
            if width is not None and scan.precursor_mz is not None:
                scan.isolation_target_mz = scan.precursor_mz
                scan.isolation_lower_offset = width / 2.0
                scan.isolation_upper_offset = width / 2.0
            self.precursor_text = None
            self.precursor_attributes = {}
        elif element == "scan" and self.scans:
            self.builder.add(self.scans.pop())


def _parse_xml(path: Path, start, end, text) -> None:
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = _reject_doctype
    if text:
        parser.CharacterDataHandler = text
    try:
        with _open_binary(path) as stream:
            while chunk := stream.read(1024 * 1024):
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
    except (OSError, expat.ExpatError) as error:
        raise ProviderError(f"Could not parse XML mass spectrometry file: {error}") from error


def _reject_doctype(*_arguments: object) -> None:
    raise ProviderError("XML document type declarations are not supported")


def _open_binary(path: Path) -> BinaryIO:
    with path.open("rb") as probe:
        is_gzip = probe.read(2) == b"\x1f\x8b"
    if path.name.lower().endswith(".gz") or is_gzip:
        return gzip.open(path, "rb")
    return path.open("rb")


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _attrs(attributes: dict[str, str]) -> dict[str, str]:
    return {_local(key): value for key, value in attributes.items()}


def _number(value: object) -> float | None:
    try:
        result = float(value) if value not in {None, ""} else None
        return result if result is None or math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _integer(value: object) -> int | None:
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _duration_seconds(value: str | None) -> float | None:
    if not value:
        return None
    match = re.fullmatch(r"PT(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?", value)
    if not match:
        return _number(value)
    minutes = float(match.group(1) or 0)
    seconds = float(match.group(2) or 0)
    return minutes * 60.0 + seconds
