"""Format-neutral spectrum access backed by Spxtacular readers."""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from threading import RLock, Thread
from typing import Any, Protocol, Self


class SpectrumAccessError(RuntimeError):
    """A spectrum request could not be fulfilled."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        super().__init__(detail)


class SpectrumLike(Protocol):
    scan_number: int | None
    ms_level: int | None
    native_id: str | None
    rt: float | None
    total_ion_current: float | None
    precursors: list[Any] | None
    mz: Any

    def to_dict(self) -> dict[str, Any]: ...


class SpectrumLookupLike(Protocol):
    def __iter__(self) -> Iterator[SpectrumLike]: ...
    def __getitem__(self, key: int | str) -> SpectrumLike: ...


class ReaderLike(Protocol):
    ms1: SpectrumLookupLike
    ms2: SpectrumLookupLike

    def __enter__(self) -> Self: ...
    def __exit__(self, *args: object) -> None: ...


ReaderFactory = Callable[[Path], ReaderLike]


@dataclass(frozen=True, slots=True)
class SpectrumSummary:
    """Small searchable descriptor that deliberately excludes peak arrays."""

    index: int
    native_id: str | None
    scan_number: int | None
    ms_level: int
    rt: float | None
    precursor_mz: float | None
    precursor_charge: int | None
    peak_count: int
    total_ion_current: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SpxtacularSpectrumSource:
    """Read one spectrum from any source supported by Spxtacular."""

    def __init__(
        self,
        storage_root: Path,
        reader_factory: ReaderFactory | None = None,
        *,
        maximum_cached_catalogs: int = 8,
        prewarm_catalogs: bool = True,
    ) -> None:
        self.storage_root = storage_root.resolve()
        self.reader_factory = reader_factory or _spxtacular_reader
        self.maximum_cached_catalogs = maximum_cached_catalogs
        self.prewarm_catalogs = prewarm_catalogs
        self._catalogs: OrderedDict[
            tuple[Path, int, int, int], tuple[SpectrumSummary, ...]
        ] = OrderedDict()
        self._catalog_warmups: set[tuple[Path, int, int, int]] = set()
        self._catalog_lock = RLock()

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
        if self.prewarm_catalogs:
            self._start_catalog_warmup(source, ms_level)
        return payload

    def browse(
        self,
        relative_path: str,
        *,
        ms_level: int,
        offset: int = 0,
        limit: int = 25,
        rt_seconds: float | None = None,
        scan_number: int | None = None,
        native_id: str | None = None,
        precursor_mz: float | None = None,
    ) -> dict[str, Any]:
        """Return a filtered page of lightweight spectrum descriptors."""
        source = self._resolve(relative_path)
        _validate_catalog_query(
            ms_level,
            offset,
            limit,
            rt_seconds,
            scan_number,
            native_id,
            precursor_mz,
        )
        catalog = self._catalog(source, ms_level)
        items, page_offset, match_index = _catalog_page(
            catalog,
            offset=offset,
            limit=limit,
            rt_seconds=rt_seconds,
            scan_number=scan_number,
            native_id=native_id,
            precursor_mz=precursor_mz,
        )
        return {
            "schema": "spectarr.spectrum-catalog",
            "schema_version": 1,
            "total": len(catalog),
            "offset": page_offset,
            "limit": limit,
            "match_index": match_index,
            "items": [item.to_dict() for item in items],
        }

    def _catalog(self, source: Path, ms_level: int) -> tuple[SpectrumSummary, ...]:
        key = self._catalog_key(source, ms_level)
        with self._catalog_lock:
            cached = self._catalogs.get(key)
            if cached is not None:
                self._catalogs.move_to_end(key)
                return cached
            try:
                with self.reader_factory(source) as reader:
                    spectra = reader.ms1 if ms_level == 1 else reader.ms2
                    catalog = tuple(
                        _spectrum_summary(spectrum, index, ms_level)
                        for index, spectrum in enumerate(spectra)
                    )
            except ImportError as error:
                raise SpectrumAccessError(503, str(error)) from error
            except (FileNotFoundError, IndexError, KeyError) as error:
                raise SpectrumAccessError(404, str(error)) from error
            except (OSError, RuntimeError, ValueError) as error:
                raise SpectrumAccessError(
                    422, f"Spxtacular could not catalog {source.name}: {error}"
                ) from error
            stale_keys = [
                cached_key
                for cached_key in self._catalogs
                if cached_key[0] == source and cached_key[3] == ms_level
            ]
            for stale_key in stale_keys:
                del self._catalogs[stale_key]
            self._catalogs[key] = catalog
            while len(self._catalogs) > self.maximum_cached_catalogs:
                self._catalogs.popitem(last=False)
            return catalog

    @staticmethod
    def _catalog_key(source: Path, ms_level: int) -> tuple[Path, int, int, int]:
        try:
            stat = source.stat()
        except OSError as error:
            raise SpectrumAccessError(404, "Spectrum source does not exist") from error
        return (source, stat.st_mtime_ns, stat.st_size, ms_level)

    def _start_catalog_warmup(self, source: Path, ms_level: int) -> None:
        try:
            key = self._catalog_key(source, ms_level)
        except SpectrumAccessError:
            return
        if not self._catalog_lock.acquire(blocking=False):
            return
        try:
            if key in self._catalogs or key in self._catalog_warmups:
                return
            self._catalog_warmups.add(key)
        finally:
            self._catalog_lock.release()

        def warm() -> None:
            try:
                self._catalog(source, ms_level)
            except SpectrumAccessError:
                pass
            finally:
                with self._catalog_lock:
                    self._catalog_warmups.discard(key)

        Thread(
            target=warm,
            name=f"spectrum-catalog-{source.name}-ms{ms_level}",
            daemon=True,
        ).start()

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


def _validate_catalog_query(
    ms_level: int,
    offset: int,
    limit: int,
    rt_seconds: float | None,
    scan_number: int | None,
    native_id: str | None,
    precursor_mz: float | None,
) -> None:
    if type(ms_level) is not int or ms_level not in {1, 2}:
        raise SpectrumAccessError(400, "ms_level must be 1 or 2")
    if type(offset) is not int or offset < 0 or offset > 10_000_000:
        raise SpectrumAccessError(400, "offset must be between 0 and 10000000")
    if type(limit) is not int or limit < 1 or limit > 100:
        raise SpectrumAccessError(400, "limit must be between 1 and 100")
    selectors = [
        rt_seconds is not None,
        scan_number is not None,
        native_id is not None,
        precursor_mz is not None,
    ]
    if sum(selectors) > 1:
        raise SpectrumAccessError(400, "Choose only one spectrum catalog search")
    for name, value in (("rt_seconds", rt_seconds), ("precursor_mz", precursor_mz)):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise SpectrumAccessError(400, f"{name} must be a nonnegative number")
    if scan_number is not None and (type(scan_number) is not int or scan_number < 0):
        raise SpectrumAccessError(400, "scan_number must be a nonnegative integer")
    if native_id is not None and (
        not isinstance(native_id, str) or not native_id or len(native_id) > 2048
    ):
        raise SpectrumAccessError(
            400, "native_id must be a nonempty string of at most 2048 characters"
        )


def _catalog_page(
    catalog: tuple[SpectrumSummary, ...],
    *,
    offset: int,
    limit: int,
    rt_seconds: float | None,
    scan_number: int | None,
    native_id: str | None,
    precursor_mz: float | None,
) -> tuple[tuple[SpectrumSummary, ...], int, int | None]:
    if not catalog:
        return (), 0, None
    if scan_number is not None:
        matches = tuple(item for item in catalog if item.scan_number == scan_number)
        return matches[:limit], 0, matches[0].index if matches else None
    if native_id is not None:
        matches = tuple(item for item in catalog if item.native_id == native_id)
        return matches[:limit], 0, matches[0].index if matches else None
    if precursor_mz is not None:
        matches = tuple(
            sorted(
                (item for item in catalog if item.precursor_mz is not None),
                key=lambda item: abs(item.precursor_mz - precursor_mz),
            )[:limit]
        )
        return matches, 0, matches[0].index if matches else None
    if rt_seconds is not None:
        candidates = tuple(item for item in catalog if item.rt is not None)
        if not candidates:
            return (), 0, None
        nearest = min(candidates, key=lambda item: abs(item.rt - rt_seconds))
        start = max(0, min(nearest.index - limit // 2, len(catalog) - limit))
        return catalog[start : start + limit], start, nearest.index
    start = min(offset, len(catalog))
    return catalog[start : start + limit], start, None


def _spectrum_summary(
    spectrum: SpectrumLike, index: int, requested_ms_level: int
) -> SpectrumSummary:
    precursors = getattr(spectrum, "precursors", None) or []
    precursor = precursors[0] if precursors else None
    return SpectrumSummary(
        index=index,
        native_id=_optional_text(getattr(spectrum, "native_id", None)),
        scan_number=_optional_int(getattr(spectrum, "scan_number", None)),
        ms_level=_optional_int(getattr(spectrum, "ms_level", None))
        or requested_ms_level,
        rt=_finite_number(getattr(spectrum, "rt", None)),
        precursor_mz=_finite_number(getattr(precursor, "mz", None)),
        precursor_charge=_optional_int(getattr(precursor, "charge", None)),
        peak_count=len(getattr(spectrum, "mz", ())),
        total_ion_current=_finite_number(getattr(spectrum, "total_ion_current", None)),
    )


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _optional_int(value: Any) -> int | None:
    return (
        int(value) if isinstance(value, int) and not isinstance(value, bool) else None
    )


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


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
