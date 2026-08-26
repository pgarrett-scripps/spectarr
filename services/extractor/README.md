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
SPECTARR_CATALOG_BATCH_SIZE=500
```

`SPECTARR_API_KEY` is accepted as an alias for `SPECTARR_SERVICE_TOKEN`.

The API and extractor must mount the same storage directory at the configured local storage root. Artifact `relative_path` values are resolved beneath that root and path escapes are rejected. During extraction, normalized spectrum metadata is posted in batches of up to 1,000 rows. A catalog becomes queryable only after its submitted row count is verified and the API activates it in the same transaction that removes the previous catalog.

## OpenMassSpec provider

The worker detects OpenMassSpec at runtime. Container builds include it by default, and built-in parsers remain the fallback for supported open formats.

```bash
python -m pip install -e 'services/extractor[openmassspec]'
```

For a smaller container without the vendor reader wheels, build with `SPECTARR_INSTALL_OPENMASSSPEC=false`.

When available, OpenMassSpec can also expose vendor RAW metadata through its unified streaming iterator. A missing or failed optional provider does not prevent mzML, mzXML, MGF, or MS2 extraction.

See the [OpenMassSpec Python quickstart](https://sigilweaver.app/openmassspec/docs/quickstart-python/) for supported readers and installation extras.

## Interactive spectrum reader

The same image provides `spectarr-spectrum-server`, a private HTTP process used by the browser GUI. It reads individual spectra with Spxtacular and returns the versioned `spxtacular.spectrum` JSON transport. Supported inputs include Thermo RAW, Bruker `.d`, mzML, MGF, MS2, and MSP according to the installed Spxtacular reader extras.

mzML access requests Spxtacular's automatic disk-backed strategy. Self-indexed gzip is used
directly, current extraction or rapidgzip caches are reused, and ordinary gzip falls back to the
mzMLPy extraction cache.

The development Compose build installs mzMLPy and Spxtacular from their published main
branches. Override `SPECTARR_MZMLPY_BUILD_CONTEXT` or
`SPECTARR_SPXTACULAR_BUILD_CONTEXT` to test a different Git ref or local BuildKit context.

```text
spectarr-spectrum-server --host 0.0.0.0 --port 8002
```

The service requires `SPECTARR_WORKER_TOKEN` on every spectrum request and resolves only storage-relative paths beneath `SPECTARR_LOCAL_STORAGE_ROOT`. Compose mounts that directory read-only. The image includes the .NET 8 runtime and configures Python.NET for Thermo RawFileReader support. Unsupported vendor RAW formats should be converted to mzML for visualization.

Spectrum selection supports a zero-based position within MS1 or MS2, a scan number, a persistent catalog row ID, or a native ID. Native IDs use keyed random access when the reader provides it. PostgreSQL stores the primary spectrum metadata catalog and supports indexed filtering with opaque keyset cursors. The reader's bounded in-memory catalog remains a compatibility path for artifacts that predate persistent catalog extraction. Neither path adds a companion file to the managed library.
