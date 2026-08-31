"""Versioned and allowlisted msconvert recipes."""

from __future__ import annotations

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


def get_recipe(name: str) -> Recipe:
    """Resolve a recipe without accepting arbitrary command arguments."""

    try:
        return RECIPES[name]
    except KeyError as error:
        choices = ", ".join(sorted(RECIPES))
        raise ValueError(f"Unknown recipe {name!r}. Available recipes: {choices}") from error


def compile_recipe(definition: dict[str, Any], overrides: dict[str, Any] | None = None) -> Recipe:
    """Compile the backend's typed recipe schema into safe msconvert arguments."""

    if definition.get("converter") != "msconvert":
        raise ValueError("Only the msconvert converter is supported")
    raw_format = str(definition.get("output_format", ""))
    formats = {value.value.lower(): value for value in OutputFormat}
    try:
        output_format = formats[raw_format.lower()]
    except KeyError as error:
        raise ValueError(f"Unsupported output format: {raw_format}") from error
    parameters = dict(definition.get("parameters") or {})
    updates = dict(overrides or {})
    unknown = set(updates) - {"filters", "mz_precision", "intensity_precision", "compression", "indexed"}
    if unknown:
        raise ValueError(f"Unsupported recipe override fields: {', '.join(sorted(unknown))}")
    parameters.update(updates)
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
