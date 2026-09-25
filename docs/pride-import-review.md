# PRIDE import review

Spectarr can import public PRIDE acquisitions directly into its existing processing pipeline. Users can search filenames, select matching acquisitions in bulk, preview available SDRF metadata, and follow downloads after leaving or reloading the page. Two files download concurrently by default. Administrators can save a limit from 1 through 8 under Settings → Downloads without restarting.

The transfer worker uses HTTPX over HTTPS with resumable staging, repository size and checksum checks, cancellation, retries, process locks, and shared disk reservations. Registration reconciles interrupted work using stable identifiers. SDRF imports append only the rows for selected acquisitions, link samples and labels to runs, preserve existing annotations, and retain a hash of the reviewed metadata. The project SDRF remains a draft for normal validation.

The implementation is ready for code review as an additive change to the current 0.3.0 source tree. This package does not assign a new release version. Release configuration, dependency constraints, and version consistency have been checked. The frontend production build succeeds.

| Validation | Result |
| --- | --- |
| Full backend suite | 154 passed, 87.94% coverage |
| Frontend component and interaction tests | 79 passed |
| Frontend type checking, lint, and build | Passed |
| Converter regression tests | 25 passed |
| Extractor regression tests | 28 passed |
| Backend Ruff and configured Mypy checks | Passed |
| Compose release configuration and dependency checks | Passed |
| Live Chromium PRIDE workflow | Passed, three acquisitions with SDRF, conversion, extraction, and actual peak retrieval |

Backend tests exercise overlapping transfers, competing workers, durable resume after killing a process, capacity reservations, cancellation, retries, access control, and registration recovery. SDRF tests cover changed preview hashes, malformed documents, ambiguous filename matches, existing rows, multiplexed labels, and retry deduplication. Settings tests verify persisted limits, live worker changes, reset to the environment default, administrator access, and read-only restore mode. Bulk selection tests cover edited names, combined filters, hidden matching rows, and the 500-file limit.

The live browser run used `PXD000561` and verified saved settings, search, bulk selection, queue reload, and three linked SDRF rows. The RAW and converted mzML catalogs agreed at 12, 228, and 5,440 spectra. The largest acquisition, `Adult_Monocytes_bRP_Velos_31_f05.raw`, contains 2,792 MS/MS spectra. The test retrieved real m/z and intensity arrays from its converted derivative. The earlier two small files contain only MS1 data. Direct source reading was verified using the production runtime's OpenMassSpec provider with the current extractor source mounted into the test container.

Application startup applies three additive migrations: `0011` creates the remote import queue, `0012` adds disk reservations, and `0013` stores the administrator's download limit. Existing installs use the environment default until a preference is saved. Deploy the backend and dashboard from the same source snapshot. Keep API processes sharing storage on the same version so they use the same worker locking scheme.

Before a production upgrade, use the existing verified backup workflow. Rollback should restore the previous database and storage snapshot with its matching application version. Downgrading these migrations directly removes the remote import queue and saved download preference. Set `SPECTARR_REMOTE_IMPORTS_ENABLED=false` if online imports must be disabled at startup. Settings → Downloads → Use server default removes only the saved concurrency override.

Scope remains public PRIDE single-file acquisitions. The live fixtures are small compared with an entire proteomics study, so they do not establish sustained multi-terabyte throughput. Conversion and extraction require their normal worker dependencies. Direct RAW reading additionally requires the optional OpenMassSpec provider. Other repositories, private access, bundled acquisitions, and ongoing SDRF synchronization remain outside this change.

See [the import guide](online-dataset-import.md) for the API, user workflow, and opt-in browser checks.
