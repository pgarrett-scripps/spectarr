"""Format-neutral spectrum access backed by Spxtacular readers."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


class SpectrumAccessError(RuntimeError):
    """A spectrum request could not be fulfilled."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        super().__init__(detail)


class SpectrumLike(Protocol):
    scan_number: int | None
    native_id: str | None

    def to_dict(self) -> dict[str, Any]: ...


class SpectrumLookupLike(Protocol):
    def __iter__(self) -> Iterator[SpectrumLike]: ...
    def __getitem__(self, key: int | str) -> SpectrumLike: ...


class ReaderLike(Protocol):
    ms1: SpectrumLookupLike
    ms2: SpectrumLookupLike

    def __enter__(self) -> ReaderLike: ...
    def __exit__(self, *args: object) -> None: ...


ReaderFactory = Callable[[Path], ReaderLike]


class SpxtacularSpectrumSource:
    """Read one spectrum from any source supported by Spxtacular."""

    def __init__(
        self, storage_root: Path, reader_factory: ReaderFactory | None = None
    ) -> None:
        self.storage_root = storage_root.resolve()
        self.reader_factory = reader_factory or _spxtacular_reader

    def read(
        self,
        relative_path: str,
        *,
        ms_level: int,
        index: int | None = None,
        scan_number: int | None = None,
        native_id: str | None = None,
    ) -> dict[str, Any]:
        source = self._resolve(relative_path)
        _validate_selection(ms_level, index, scan_number, native_id)
        try:
            with self.reader_factory(source) as reader:
                spectra = reader.ms1 if ms_level == 1 else reader.ms2
                spectrum = _select_spectrum(
                    spectra, ms_level, index, scan_number, native_id
                )
        except SpectrumAccessError:
            raise
        except ImportError as error:
            raise SpectrumAccessError(503, str(error)) from error
        except (FileNotFoundError, IndexError, KeyError) as error:
            raise SpectrumAccessError(404, str(error)) from error
        except (OSError, RuntimeError, ValueError) as error:
            raise SpectrumAccessError(
                422, f"Spxtacular could not read {source.name}: {error}"
            ) from error
        payload = _spectrum_payload(spectrum)
        if (
            payload.get("schema") != "spxtacular.spectrum"
            or payload.get("schema_version") != 1
        ):
            raise SpectrumAccessError(
                502, "Spxtacular returned an unsupported spectrum transport payload"
            )
        return payload

    def _resolve(self, relative_path: str) -> Path:
        if not relative_path or Path(relative_path).is_absolute():
            raise SpectrumAccessError(
                400, "relative_path must be a storage-relative path"
            )
        candidate = (self.storage_root / relative_path).resolve(strict=False)
        if not candidate.is_relative_to(self.storage_root):
            raise SpectrumAccessError(
                403, "Spectrum source escapes the configured storage root"
            )
        try:
            source = candidate.resolve(strict=True)
        except OSError as error:
            raise SpectrumAccessError(404, "Spectrum source does not exist") from error
        if not source.is_relative_to(self.storage_root):
            raise SpectrumAccessError(
                403, "Spectrum source resolves outside the configured storage root"
            )
        if not source.is_file() and not source.is_dir():
            raise SpectrumAccessError(
                422, "Spectrum source is not a file or directory bundle"
            )
        return source


def _spxtacular_reader(path: Path) -> ReaderLike:
    try:
        from spxtacular import Reader
    except ImportError as error:
        raise ImportError(
            "Spxtacular is not installed in the spectrum-reader image"
        ) from error
    return Reader(path, mzml_gzip_mode="auto", mzml_in_memory=False)


def _validate_selection(
    ms_level: int,
    index: int | None,
    scan_number: int | None,
    native_id: str | None,
) -> None:
    if type(ms_level) is not int or ms_level not in {1, 2}:
        raise SpectrumAccessError(400, "ms_level must be 1 or 2")
    selectors = [index is not None, scan_number is not None, native_id is not None]
    if sum(selectors) != 1:
        raise SpectrumAccessError(
            400, "Exactly one of index, scan_number, or native_id is required"
        )
    if index is not None and (
        type(index) is not int or index < 0 or index > 10_000_000
    ):
        raise SpectrumAccessError(
            400, "index must be an integer between 0 and 10000000"
        )
    if scan_number is not None and (type(scan_number) is not int or scan_number < 0):
        raise SpectrumAccessError(400, "scan_number must be a nonnegative integer")
    if native_id is not None and (
        not isinstance(native_id, str) or not native_id or len(native_id) > 2048
    ):
        raise SpectrumAccessError(
            400, "native_id must be a nonempty string of at most 2048 characters"
        )


def _select_spectrum(
    spectra: SpectrumLookupLike,
    ms_level: int,
    index: int | None,
    scan_number: int | None,
    native_id: str | None,
) -> SpectrumLike:
    if native_id is not None:
        try:
            spectrum = spectra[native_id]
        except (IndexError, KeyError) as error:
            raise SpectrumAccessError(
                404, f"No matching MS{ms_level} spectrum was found for {native_id!r}"
            ) from error
        except (NotImplementedError, TypeError):
            pass
        else:
            if (
                spectrum.native_id == native_id
                and getattr(spectrum, "ms_level", ms_level) == ms_level
            ):
                return spectrum
            raise SpectrumAccessError(
                404, f"No matching MS{ms_level} spectrum was found for {native_id!r}"
            )
    for position, spectrum in enumerate(spectra):
        if index is not None and position == index:
            return spectrum
        if scan_number is not None and spectrum.scan_number == scan_number:
            return spectrum
        if native_id is not None and spectrum.native_id == native_id:
            return spectrum
    selector = (
        f"index {index}"
        if index is not None
        else f"scan {scan_number}"
        if scan_number is not None
        else repr(native_id)
    )
    raise SpectrumAccessError(404, f"No matching spectrum was found for {selector}")


def _spectrum_payload(spectrum: SpectrumLike) -> dict[str, Any]:
    """Use the public transport API with a bridge for Spxtacular 0.5.0."""
    to_dict = getattr(spectrum, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    metadata = spectrum._meta_dict()  # type: ignore[attr-defined]
    kind = "msn_spectrum" if hasattr(spectrum, "ms_level") else "spectrum"
    return {
        "schema": "spxtacular.spectrum",
        "schema_version": 1,
        "kind": kind,
        "arrays": {
            "mz": _json_value(spectrum.mz, "arrays.mz"),  # type: ignore[attr-defined]
            "intensity": _json_value(spectrum.intensity, "arrays.intensity"),  # type: ignore[attr-defined]
            "charge": _json_value(getattr(spectrum, "charge", None), "arrays.charge"),
            "im": _json_value(getattr(spectrum, "im", None), "arrays.im"),
            "iso_score": _json_value(
                getattr(spectrum, "iso_score", None), "arrays.iso_score"
            ),
        },
        "metadata": _json_value(metadata, "metadata"),
    }


def _json_value(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SpectrumAccessError(502, f"{path} contains a non-finite number")
        return value
    if isinstance(value, Enum):
        return _json_value(value.value, path)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value), path)
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item, f"{path}.{key}") for key, item in value.items()
        }
    if hasattr(value, "tolist"):
        return _json_value(value.tolist(), path)
    if hasattr(value, "item"):
        return _json_value(value.item(), path)
    if isinstance(value, (list, tuple)):
        return [
            _json_value(item, f"{path}[{position}]")
            for position, item in enumerate(value)
        ]
    raise SpectrumAccessError(
        502, f"{path} contains unsupported type {type(value).__name__}"
    )
