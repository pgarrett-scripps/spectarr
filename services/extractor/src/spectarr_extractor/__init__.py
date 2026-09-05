"""Spectarr metadata extraction service."""

__version__ = "0.3.0"

from .models import ExtractionResult, SpectrumObservation
from .providers import ProviderRegistry

__all__ = ["ExtractionResult", "ProviderRegistry", "SpectrumObservation", "__version__"]
