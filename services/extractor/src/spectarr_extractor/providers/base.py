"""Parser provider interfaces."""

from __future__ import annotations

import gzip
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..models import ExtractionResult, SpectrumObservation


class ProviderError(RuntimeError):
    """A file could not be parsed by a provider."""


class ProviderUnavailable(ProviderError):
    """An optional provider is not installed or cannot load."""


class ParserProvider(Protocol):
    name: str
    version: str
    optional: bool

    def supports(self, path: Path, declared_format: str | None = None) -> bool: ...

    def extract(
        self,
        path: Path,
        declared_format: str | None = None,
        on_spectrum: Callable[[SpectrumObservation], None] | None = None,
    ) -> ExtractionResult: ...


def normalized_format(path: Path, declared_format: str | None) -> str:
    suffix_path = Path(path.stem) if path.suffix.lower() == ".gz" else path
    value = declared_format or suffix_path.suffix.lstrip(".")
    if value.lower().endswith(".gz"):
        value = value[:-3]
    names = {"mzml": "mzML", "mzxml": "mzXML", "mgf": "MGF", "ms2": "MS2"}
    return names.get(value.lower(), value)



def open_text_spectrum(path: Path):
    """Stream plain or gzip peak lists, including content-addressed objects."""
    with path.open("rb") as probe:
        compressed = probe.read(2) == b"\x1f\x8b"
    if compressed or path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")
