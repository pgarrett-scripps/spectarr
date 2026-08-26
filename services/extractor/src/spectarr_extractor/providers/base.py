"""Parser provider interfaces."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

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
    if not declared_format and path.name.lower().endswith(".mzml.gz"):
        value = "mzml.gz"
    else:
        value = declared_format or path.suffix.lstrip(".")
    if value.lower() == "mzml.gz":
        return "mzML"
    names = {"mzml": "mzML", "mzxml": "mzXML", "mgf": "MGF", "ms2": "MS2"}
    return names.get(value.lower(), value)
