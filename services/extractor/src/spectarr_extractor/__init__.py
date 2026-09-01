"""Spectarr metadata extraction service."""

__version__ = "0.2.0"

from .models import ExtractionResult, SpectrumObservation
from .providers import ProviderRegistry

__all__ = ["ExtractionResult", "ProviderRegistry", "SpectrumObservation", "__version__"]
