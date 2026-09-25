# Review fixes, 2026-09-25

The four findings from the September 25 code review are addressed.

- Startup refreshes existing run manifest observations using the same selection policy as the API. It corrects acquisition dates and adds summary provenance, preserves other manifest fields, and leaves artifact names, paths, inodes, and bytes alone. Each manifest replacement is atomic. An interrupted refresh resumes on the next startup. Restore verification mode skips this refresh.
- Project updates and library rebuilds return HTTP 409 while a processing job is running. The check happens before the exclusive storage lock, so existing workers can continue polling, publishing results, and renewing leases. A separate job-start lock prevents a claim or job state update from starting processing between the idle check and publication. Queued jobs can start after the rebuild finishes.
- Run pages refresh immediately after queuing conversion or extraction and monitor submitted jobs until completion, failure, or cancellation. Runs already marked as processing also refresh periodically. Polling stops when the work finishes or the page is left. Late submission responses from a different run are ignored.
- Run route canonicalization waits until the loaded record matches the requested run ID, preventing a previous run from redirecting navigation to the wrong project.

Validation: the full backend suite passed 187 tests at 88.75% coverage. A subsequent focused run passed all 16 correctness tests, including the additional concurrent claim regression, for 188 distinct backend tests checked. All 125 frontend tests, frontend type checking, lint, and production build passed. Backend lint and configured type checking passed.

Deployed image `d0d3f189d82d` is healthy. All 20 live run manifests now match the API summaries and provenance. All 45 acquisition file entries retain their paths, inodes, sizes, and modification times. The catalog retains 9 projects, 20 runs, and 44 artifacts. The backup at `storage/upgrade-backups/review-fixes-20260925` verified all 36 required object files. The existing processing page was refreshed to the new frontend bundle.
