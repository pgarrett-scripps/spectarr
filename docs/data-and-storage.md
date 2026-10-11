# Formats and storage

Spectarr stores source acquisitions as immutable, checksummed artifacts. It provides a human-readable library alongside the content-addressed objects, so analysis tools can continue using ordinary files and folders.

## Supported data

| Data | Import | Metadata and spectra | Conversion |
| --- | --- | --- | --- |
| mzML and gzipped mzML | File upload or server import | Native reader | Yes |
| mzXML | File upload or server import | Native reader | Yes |
| MGF | File upload or server import | Native reader | Yes |
| MS2 | File upload or server import | Native reader | Yes |
| Thermo RAW | File upload, server import, or agent | Direct reader or mzML derivative | Vendor reader required |
| Bruker `.d` | Atomic directory acquisition | Direct reader or mzML derivative | Vendor reader required |
| Other ProteoWizard vendor formats | Depends on acquisition packaging | Usually via mzML derivative | Depends on vendor support |

Format recognition does not guarantee support for every instrument model or acquisition mode. See [recorded acceptance testing](acceptance-testing.md) and validate representative files from your instruments.

Derivatives record source checksums, processing profiles, tool versions, parameters, and output checksums. A reclaimed derivative retains its provenance so it can be regenerated.

## Where the data goes

The default Compose installation keeps durable state under `data/`:

```text
data/
├── .spectarr/            # Generated secrets and storage identity
├── spectarr.db          # SQLite metadata
└── storage/
    ├── objects/         # Checksummed acquisitions and derivatives
    └── library/         # Readable project folders
```

The library uses stable identifiers to disambiguate human-readable names:

```text
library/
└── plasma-proteomics__7fc2a010/
    ├── raw/
    │   └── patient-01__sample-a__31d62c18.raw
    ├── mzml/
    │   └── patient-01__sample-a__31d62c18.mzML
    └── .spectarr/
        └── runs/
            └── 31d62c18-....json
```

On the same filesystem, library files can be hard links without consuming duplicate space. Treat the managed library as read-only. Editing a hard link also changes its underlying object. Use copies for tools that modify input files.

See [file discovery](agent-file-discovery.md) for stable IDs, server paths, directory bundles, and container path mapping.

## Deployment boundaries

The dashboard, API, reader, and workers share one application container. ProteoWizard conversions run in separate containers through the mounted Docker socket. The published image targets Linux x86-64. Windows and Linux instrument agents upload to the server.

Keep SQLite on a local filesystem. Bulk artifacts may live on separate storage through `SPECTARR_STORAGE_DIR`. Follow the [installation guide](../release/README.md#storage-and-permissions) before changing an existing deployment's paths.

The agent uses inactivity and temporary-file markers to detect completed acquisitions. Inactivity is a heuristic. Validate how your instrument writes, pauses, closes, and renames files before unattended collection.
