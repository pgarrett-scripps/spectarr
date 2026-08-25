# Spectarr metadata extractor

This worker claims `extract_metadata` jobs and streams through mass spectrometry artifacts without loading a complete run into memory. It supports mzML, mzXML, MGF, and MS2 with built-in parsers.

Each result uses schema version `1.0` and includes:

- Total spectra and counts by MS level
- Polarity and centroid or profile representation
- Retention time, acquisition duration, and m/z ranges
- Precursor m/z, charge, and collision energy summaries
- Bounded TIC and BPC previews
- Peak count, TIC, and BPC statistics
- Ion mobility ranges when present
- DIA isolation windows when present
- Parser warnings and provenance

## Run locally

```bash
python -m pip install -e services/extractor
spectarr-extractor-worker
```

Configuration:

```text
SPECTARR_API_URL=http://api:8000
SPECTARR_LOCAL_STORAGE_ROOT=/data/storage
SPECTARR_WORKER_ID=extractor-1
SPECTARR_SERVICE_TOKEN=
SPECTARR_WORKER_TOKEN=
SPECTARR_HEARTBEAT_SECONDS=10
```

`SPECTARR_API_KEY` is accepted as an alias for `SPECTARR_SERVICE_TOKEN`.

The API and extractor must mount the same storage directory at the configured local storage root. Artifact `relative_path` values are resolved beneath that root and path escapes are rejected.

## OpenMassSpec provider

The worker detects OpenMassSpec at runtime. Container builds include it by default, and built-in parsers remain the fallback for supported open formats.

```bash
python -m pip install -e 'services/extractor[openmassspec]'
```

For a smaller container without the vendor reader wheels, build with `SPECTARR_INSTALL_OPENMASSSPEC=false`.

When available, OpenMassSpec can also expose vendor RAW metadata through its unified streaming iterator. A missing or failed optional provider does not prevent mzML, mzXML, MGF, or MS2 extraction.

See the [OpenMassSpec Python quickstart](https://sigilweaver.app/openmassspec/docs/quickstart-python/) for supported readers and installation extras.
