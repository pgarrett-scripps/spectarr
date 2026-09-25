# Focused correctness pass, 2026-09-24

Implemented the three correctness fixes identified in the project review. These changes were subsequently deployed with a verified backup. The [September 25 follow-up](review-fixes-2026-09-25.md) addresses existing manifest refresh, active-worker protection, and run-page updates.

## Scientific summaries

Run summaries, run QC, and generated run manifests now use one explicit policy, `source_then_linked_open_format/v1`.

1. Prefer metadata observations from a source artifact. If multiple sources have observations, choose the earliest source, then its latest metadata result.
2. If no source has observations, allow a derived mzML or mzXML whose `parent_artifact_id` points directly to a source in the same run.
3. Otherwise leave the summary unknown. Filtered MGF, unlinked derivatives, and analysis results do not supply acquisition summaries.

Responses expose `summary_basis`, including the selected artifact ID and selection reason. The dashboard names the observed file and explains converted-file fallbacks. These observations describe that representation. A linked mzML can still differ from the original acquisition.

Extraction results remain artifact-specific. Extraction no longer copies metrics onto the run. Legacy run-level metric caches are ignored when serving summaries, preventing stale derivative counts from returning. Other run metadata is preserved. Zero counts and zero duration remain distinguishable from missing values.

Existing unlinked converted files may now show unknown acquisition summaries until their provenance is established through a supported workflow. No parent links are inferred from filenames. An additional regression caught and fixed event deduplication collisions between metadata and analysis results from the same extractor.

## Acquisition dates

An unknown acquisition timestamp stays null in the API and manifests, displays as Unknown, and exports as an empty CSV cell. Import time remains a separate field. CSV exports now include both Acquired and Imported columns. Known acquisition timestamps are preserved.

The optional filename token `{acquired_date}` expands to `unknown` when the date is unavailable. `{imported_date}` explicitly requests the import date. Existing materialized names are not silently changed.

## Library rebuild and project rename

Rebuilds preflight object availability, prepare a complete replacement beside the current library, and flush the staged files and directory entries before publication. The previous directory remains available for rollback until publication and the database transaction succeed. Project rename and replacement artifact paths share that transaction.

API rebuilds and project updates hold the exclusive maintenance lock. A durable publication journal and database audit record distinguish committed from interrupted work. Startup and subsequent API requests recover pending publications. A generation marker prevents recovery from deleting the previous view if the committed replacement is unexpectedly missing or incorrect.

Before database commit, recovery restores the previous library. After commit, recovery retains the replacement and finishes cleanup. Missing object files or bundle members fail preflight without deleting existing readable copies.

This is recoverable publication, not uninterrupted filesystem access. External readers can briefly encounter a missing root during the directory swap and should retry resolution. Copy mode needs space for the replacement alongside the old view. A library root that is itself a mount point is rejected before publication. Configure a directory inside that volume. Direct Python callers of `rebuild` must hold the exclusive maintenance lock and account for its ownership of the supplied transaction's commit.

## Verification

| Check | Result |
| --- | --- |
| Full backend suite | 185 passed, 88.60% coverage |
| Frontend tests | 120 passed |
| Backend lint and configured type checks | Passed |
| Additional type checks for selection and publication modules | Passed |
| Frontend type checks, lint, production build | Passed |
| Chromium against an isolated real API | Fallback labels, source precedence, zero values, unknown dates, CSV separation, and rename followed by real file resolution passed |

Failure tests inject copy, manifest, publication, and database commit errors. Interruption tests reopen sessions and recover at build, old-directory move, new-directory publication, and post-commit boundaries. Tests also cover missing committed generations, exclusive locking, and copied vendor bundles.

These checks use synthetic acquisitions and temporary storage. They do not establish scientific equivalence across real vendor conversions, network-filesystem crash behavior, or production-scale disk requirements. Real source and derivative pairs remain the next acceptance gate before relying on run summaries for scientific decisions.
