"""Versioned and allowlisted msconvert recipes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import OutputFormat


@dataclass(frozen=True)
class Recipe:
    """A normalized conversion recipe."""

    name: str
    version: int
    output_format: OutputFormat
    arguments: tuple[str, ...]
    config_path: Path | None = None
    converter: str = "msconvert"
    converter_version: str | None = None


RECIPES: dict[str, Recipe] = {
    "archival-mzml-v1": Recipe(
        name="archival-mzml-v1",
        version=1,
        output_format=OutputFormat.MZML,
        arguments=("--mzML", "--64", "--zlib"),
    ),
    "search-mgf-v1": Recipe(
        name="search-mgf-v1",
        version=1,
        output_format=OutputFormat.MGF,
        arguments=("--mgf", "--filter", "peakPicking vendor msLevel=1-", "--filter", "msLevel 2"),
    ),
    "search-ms2-v1": Recipe(
        name="search-ms2-v1",
        version=1,
        output_format=OutputFormat.MS2,
        arguments=("--ms2", "--filter", "peakPicking vendor msLevel=1-", "--filter", "msLevel 2"),
    ),
}


def _loss_policy_arguments(value: Any) -> list[str]:
    """Validate every policy field before compiling enforceable native arguments."""
    if not isinstance(value, dict) or set(value) - {"deny", "require"}:
        raise ValueError("loss_policy must be an object with only deny and require fields")
    choices = {
        "deny": {"unqualified-calibration", "precision-reduction", "mobility-collapse", "chromatogram-loss", "reduced-text-metadata", "experimental-processing"},
        "require": {"polarity", "precursor-isolation", "precursor-target", "collision-energy", "precursor-charge"},
    }
    arguments = []
    for name, flag in (("deny", "--forbid-loss"), ("require", "--require-field")):
        items = value.get(name, [])
        if not isinstance(items, list) or any(not isinstance(item, str) or item not in choices[name] for item in items):
            raise ValueError(f"loss_policy.{name} must be a list of supported policy names")
        for item in items:
            arguments.extend([flag, item])
    return arguments


def get_recipe(name: str) -> Recipe:
    """Resolve a recipe without accepting arbitrary command arguments."""

    try:
        return RECIPES[name]
    except KeyError as error:
        choices = ", ".join(sorted(RECIPES))
        raise ValueError(f"Unknown recipe {name!r}. Available recipes: {choices}") from error


def compile_recipe(definition: dict[str, Any], overrides: dict[str, Any] | None = None) -> Recipe:
    """Compile the backend's typed recipe schema into safe msconvert arguments."""

    if definition.get("converter") == "msconvert-rs":
        return _compile_native_recipe(definition, overrides)
    if definition.get("converter") != "msconvert":
        raise ValueError("Only the msconvert converter and explicit msconvert-rs backend are supported")
    raw_format = str(definition.get("output_format", ""))
    formats = {value.value.lower(): value for value in OutputFormat}
    try:
        output_format = formats[raw_format.lower()]
    except KeyError as error:
        raise ValueError(f"Unsupported output format: {raw_format}") from error
    parameters = dict(definition.get("parameters") or {})
    if "allow_approximate_calibration" in parameters and not isinstance(parameters["allow_approximate_calibration"], bool):
        raise ValueError("allow_approximate_calibration must be boolean")
    native_only = {"stored_centroids", "allow_experimental", "allow_approximate_calibration", "mobility_collapse", "bruker_frames", "require_retention_time", "require_mobility", "timeout_seconds", "max_memory_mb", "max_temp_bytes", "max_decompressed_bytes", "max_peaks_per_spectrum"}
    if any(parameters.get(name) for name in native_only) or parameters.get("compression_level") is not None:
        raise ValueError("Native conversion policies require an explicit msconvert-rs recipe")
    updates = dict(overrides or {})
    unknown = set(updates) - {"filters", "mz_precision", "intensity_precision", "compression", "indexed", "loss_policy"}
    if unknown:
        raise ValueError(f"Unsupported recipe override fields: {', '.join(sorted(unknown))}")
    parameters.update(updates)
    if _loss_policy_arguments(parameters.get("loss_policy", {})):
        raise ValueError("Loss policies require an explicit msconvert-rs recipe")
    preset_name = parameters.get("preset")
    if preset_name:
        try:
            from msconvert_cli.presets import PresetConfig, get_preset_config_path
        except ImportError as error:
            raise ValueError("MSCLI named presets are unavailable in this worker") from error
        preset = PresetConfig.from_name(str(preset_name))
        if preset is None:
            raise ValueError(f"Unknown MSCLI preset: {preset_name}")
        config_path = get_preset_config_path(preset)
        if config_path is None:
            raise ValueError(f"MSCLI preset config is missing: {preset_name}")
        return Recipe(
            name=str(definition.get("name") or preset_name),
            version=int(definition.get("revision") or 1),
            output_format=output_format,
            arguments=(),
            config_path=config_path,
        )
    arguments = [f"--{output_format.value}"]
    mz_precision = _precision(parameters.get("mz_precision", 64), "m/z")
    intensity_precision = _precision(parameters.get("intensity_precision", 32), "intensity")
    arguments.extend([f"--mz{mz_precision}", f"--inten{intensity_precision}"])
    compression = parameters.get("compression", "zlib")
    if compression == "zlib":
        arguments.append("--zlib")
    elif compression == "numpress":
        arguments.extend(["--numpressLinear", "--numpressSlof"])
    elif compression != "none":
        raise ValueError(f"Unsupported compression: {compression}")
    if parameters.get("indexed", True) is False:
        arguments.append("--noindex")
    for filter_value in parameters.get("filters", []):
        arguments.extend(["--filter", _compile_filter(filter_value)])
    return Recipe(
        name=str(definition.get("name") or "api-recipe"),
        version=int(definition.get("revision") or 1),
        output_format=output_format,
        arguments=tuple(arguments),
    )


def _precision(value: Any, label: str) -> int:
    if value not in {32, 64}:
        raise ValueError(f"{label} precision must be 32 or 64")
    return int(value)


def _compile_filter(filter_value: Any) -> str:
    if not isinstance(filter_value, dict):
        raise ValueError("Conversion filters must be typed objects")
    kind = filter_value.get("kind")
    if kind == "peak_picking":
        algorithm = str(filter_value.get("algorithm", "vendor"))
        if not algorithm.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Invalid peak picking algorithm")
        levels = _levels(filter_value.get("ms_levels", [1, 2]))
        return f"peakPicking {algorithm} msLevel={levels}"
    if kind == "ms_level":
        return f"msLevel {_levels(filter_value.get('levels'))}"
    if kind == "threshold":
        threshold_type = filter_value.get("threshold_type")
        orientation = filter_value.get("orientation", "most-intense")
        if threshold_type not in {"count", "absolute", "relative"}:
            raise ValueError("Invalid threshold type")
        if orientation not in {"most-intense", "least-intense"}:
            raise ValueError("Invalid threshold orientation")
        value = float(filter_value["value"])
        if value < 0:
            raise ValueError("Threshold value cannot be negative")
        return f"threshold {threshold_type} {value:g} {orientation}"
    raise ValueError(f"Unsupported conversion filter: {kind}")


def _levels(value: Any) -> str:
    if not isinstance(value, list) or not value:
        raise ValueError("MS levels must be a nonempty list")
    levels = sorted({int(level) for level in value})
    if any(level < 1 or level > 10 for level in levels):
        raise ValueError("MS levels must be between 1 and 10")
    if len(levels) == 1:
        return str(levels[0])
    if levels != list(range(levels[0], levels[-1] + 1)):
        raise ValueError("MS levels must form a continuous range")
    return f"{levels[0]}-{levels[-1]}"


def _compile_native_recipe(definition: dict[str, Any], overrides: dict[str, Any] | None) -> Recipe:
    """Compile the explicitly selected native backend without translating vendor algorithms."""

    supported = {"mzml": OutputFormat.MZML, "mgf": OutputFormat.MGF, "ms2": OutputFormat.MS2}
    output_format = supported.get(str(definition.get("output_format", "")).lower())
    if output_format is None:
        raise ValueError("msconvert-rs supports mzML, MGF and MS2 output")
    flags = {
        "stored_centroids", "allow_experimental", "allow_approximate_calibration", "mobility_collapse", "bruker_frames",
        "require_retention_time", "require_mobility",
    }
    limits = {"timeout_seconds", "max_memory_mb", "max_temp_bytes", "max_decompressed_bytes", "max_peaks_per_spectrum"}
    allowed = flags | limits | {"preset", "filters", "mz_precision", "intensity_precision", "compression", "compression_level", "indexed", "loss_policy"}
    parameters = dict(definition.get("parameters") or {})
    parameters.update(overrides or {})
    unknown = set(parameters) - allowed
    if unknown:
        raise ValueError(f"Unsupported native recipe fields: {', '.join(sorted(unknown))}")
    if parameters.get("preset"):
        raise ValueError("ProteoWizard presets cannot run on the native backend")
    if parameters.get("compression", "zlib") not in {"zlib", "none"}:
        raise ValueError("Native backend does not support Numpress compression")
    arguments = [f"--{output_format.value}"]
    arguments.extend(_loss_policy_arguments(parameters.get("loss_policy", {})))
    if parameters.get("allow_approximate_calibration") and parameters.get("allow_experimental") is not True:
        raise ValueError("allow_approximate_calibration requires allow_experimental")
    compression_level = parameters.get("compression_level")
    if compression_level is not None:
        if isinstance(compression_level, bool) or not isinstance(compression_level, int) or not 0 <= compression_level <= 9:
            raise ValueError("compression_level must be an integer from 0 to 9")
        if output_format != OutputFormat.MZML or parameters.get("compression", "zlib") != "zlib":
            raise ValueError("compression_level requires native mzML with zlib")
        arguments.extend(["--compression-level", str(compression_level)])
    if output_format == OutputFormat.MZML:
        for name, prefix, default in [("mz_precision", "mz", 64), ("intensity_precision", "inten", 64)]:
            arguments.append(f"--{prefix}{_precision(parameters.get(name, default), name)}")
        compression = parameters.get("compression", "zlib")
        if compression not in {"zlib", "none"}:
            raise ValueError("Native backend does not support Numpress compression")
        arguments.append("--zlib" if compression == "zlib" else "--no-zlib")
        if parameters.get("indexed", True) is False:
            arguments.append("--noindex")
    for name in sorted(flags):
        value = parameters.get(name, False)
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be boolean")
        if value:
            arguments.append("--" + name.replace("_", "-"))
    for name in sorted(limits):
        value = parameters.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
        if value:
            arguments.extend(["--" + name.replace("_", "-"), str(value)])
    for item in parameters.get("filters", []):
        if not isinstance(item, dict):
            raise ValueError("Native filters must be typed objects")
        if item.get("kind") == "peak_picking":
            raise ValueError("Typed ProteoWizard peak picking cannot run on msconvert-rs. Select stored_centroids explicitly for stored Thermo peaks")
        if item.get("kind") == "threshold":
            if item.get("threshold_type") not in {"absolute", "count"} or item.get("orientation", "most-intense") != "most-intense":
                raise ValueError("Native typed threshold supports absolute or count, most-intense only")
            if not math.isfinite(float(item["value"])):
                raise ValueError("Native threshold must be finite")
            if item["threshold_type"] == "count" and not float(item["value"]).is_integer():
                raise ValueError("Native peak count must be an integer")
        arguments.extend(["--filter", _compile_filter(item)])
    return Recipe(
        name=str(definition.get("name") or "native-recipe"),
        version=int(definition.get("revision") or 1),
        output_format=output_format,
        arguments=tuple(arguments),
        converter="msconvert-rs",
        converter_version=str(definition["converter_version"]) if definition.get("converter_version") else None,
    )
