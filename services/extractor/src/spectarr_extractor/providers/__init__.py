"""Parser provider selection with optional OpenMassSpec support."""

from __future__ import annotations

from pathlib import Path

from ..models import ExtractionResult
from .base import ParserProvider, ProviderError, ProviderUnavailable
from .mgf import MgfProvider
from .ms2 import Ms2Provider
from .openmassspec import OpenMassSpecProvider
from .xml_formats import MzmlProvider, MzxmlProvider


class ProviderRegistry:
    """Select providers by capability and fall back after optional-provider failures."""

    def __init__(self, providers: list[ParserProvider] | None = None) -> None:
        self.providers = providers or [
            OpenMassSpecProvider(),
            MzmlProvider(),
            MzxmlProvider(),
            MgfProvider(),
            Ms2Provider(),
        ]

    def extract(self, path: Path, declared_format: str | None = None) -> ExtractionResult:
        failures: list[str] = []
        candidates = [provider for provider in self.providers if provider.supports(path, declared_format)]
        if not candidates:
            raise ProviderError(f"No metadata provider supports {declared_format or path.suffix or path.name}")
        for provider in candidates:
            try:
                result = provider.extract(path, declared_format)
            except ProviderUnavailable as error:
                failures.append(f"{provider.name} unavailable: {error}")
                continue
            except ProviderError as error:
                if provider.optional:
                    failures.append(f"{provider.name} failed: {error}")
                    continue
                raise
            result.warnings[:0] = failures
            return result
        raise ProviderError(". ".join(failures) or "All matching metadata providers failed")


__all__ = ["ParserProvider", "ProviderError", "ProviderRegistry", "ProviderUnavailable"]
