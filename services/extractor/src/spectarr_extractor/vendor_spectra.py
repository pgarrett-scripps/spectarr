"""Read vendor spectra with the same native identities used by extraction."""

from __future__ import annotations

from pathlib import Path

from .providers.openmassspec import OpenMassSpecProvider, _attribute, _polarity, _representation


class VendorSpectrumReader:
    """Adapt OpenMassSpec records to the application's spectrum transport."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.ms1 = VendorSpectrumLookup(path, 1)
        self.ms2 = VendorSpectrumLookup(path, 2)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class VendorSpectrumLookup:
    def __init__(self, path: Path, ms_level: int) -> None:
        self.path = path
        self.ms_level = ms_level

    def __getitem__(self, _key):
        # The provider exposes streaming records, not a native ID index.
        raise NotImplementedError

    def __iter__(self):
        module = OpenMassSpecProvider._module()
        records = iter(module.iter_spectra(str(self.path)))
        try:
            for record in records:
                if record.ms_level == self.ms_level:
                    yield to_spectrum(record)
        finally:
            close = getattr(records, 'close', None)
            if close:
                close()


def to_spectrum(record):
    """Preserve raw record identity and arrays without aggregating frames or precursors."""
    from spxtacular import MsnSpectrum, Precursor

    # OpenMassSpec array getters transfer ownership, so read each exactly once.
    mz = record.mz
    intensity = record.intensity
    mobility = _attribute(record, 'inv_mobility_per_peak')
    if mobility is not None and len(mobility) != len(mz):
        mobility = None
    precursor = _attribute(record, 'precursor')
    precursors = None
    precursor_mz = _attribute(precursor, 'mz', 'selected_ion_mz', 'target_mz')
    if precursor_mz is not None:
        precursors = [Precursor(
            precursor_mz=precursor_mz,
            intensity=_attribute(precursor, 'intensity') or 0.0,
            charge=_attribute(precursor, 'charge'),
            im=_attribute(record, 'inv_mobility'),
            im_type='ook0' if _attribute(record, 'inv_mobility') is not None else None,
            is_monoisotopic=None,
        )]
    target = _attribute(precursor, 'isolation_target_mz', 'target_mz')
    width = _attribute(precursor, 'isolation_width')
    isolation = (target - width / 2, target + width / 2) if target is not None and width is not None else None
    low = _attribute(record, 'low_mz')
    high = _attribute(record, 'high_mz')
    return MsnSpectrum(
        mz=mz, intensity=intensity, im=mobility,
        scan_number=record.scan_number,
        native_id=record.native_id,
        ms_level=record.ms_level,
        rt=record.retention_time_sec,
        spectrum_type=_representation(_attribute(record, 'scan_mode')),
        polarity=_polarity(_attribute(record, 'polarity')),
        injection_time=_attribute(record, 'ion_injection_time_ms'),
        total_ion_current=_attribute(record, 'total_ion_current'),
        mz_range=(low, high) if low is not None and high is not None else None,
        im_type='ook0' if mobility is not None or _attribute(record, 'inv_mobility') is not None else None,
        precursors=precursors,
        collision_energy=_attribute(precursor, 'collision_energy'),
        activation_type=_attribute(precursor, 'activation_type'),
        isolation_mz_range=isolation,
    )
