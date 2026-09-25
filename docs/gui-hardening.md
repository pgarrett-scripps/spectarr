# GUI hardening verification

This pass used the production frontend build with a disposable password-authenticated API, SQLite database, storage, extractor worker, and spectrum reader. No production data was changed.

## Fixes

- Long dialogs scroll within the viewport, keeping controls reachable with large profile and experiment lists.
- Dialogs contain keyboard tab navigation, support Escape when closing is allowed, and restore focus to their opener.
- Pending create, move, delete, processing, account, agent, and token operations disable conflicting controls.
- Structured API validation errors display field messages. All file downloads handle expired sessions consistently. A late unauthorized response cannot erase a newer login.
- Signing out clears the local session even when the server request fails.
- Unsupported settings sections fall back to Security. Viewer accounts cannot open administrator pages or import screens, edit project metadata, or queue processing. Integration pages avoid administrator-only requests for viewers.
- SDRF validation and export first save current table edits. Table changes invalidate old validation, package download requires a validated saved revision, column headings retain focus while typing, and imports can retry the same file.
- Project metadata editors reset when the project route changes.
- Processing preview changes discard prior results and ignore late responses. Failed previews cannot queue jobs. Clearing all selected profiles stays cleared.
- Metadata-only automation rules work. Editing simple profile settings preserves advanced filters. Named conversion presets constrain the output format.
- Expanded processing batches refresh during active work and display detail request failures.
- Loaded resources survive transient refresh failures. Unsaved backup policy edits remain intact, with backup actions disabled until status recovers.
- Webhook mutations prevent overlapping updates and clear recovered errors.
- Activity supports queued-job cancellation and hides write actions from viewers. The cancellation endpoint atomically checks queued state, preserving work that has already started or completed. Cancelled jobs retain their own status and can be retried.
- Cancelled processing batches have a retry control.
- Storage previews clear earlier confirmations and lock their target while calculating. Move dialogs display destination loading failures with a retry action.

## Verification

The extended pass also passed 155 backend tests with 88.21% coverage, plus 33 agent, 25 converter, 28 extractor, 26 MCP, and 17 webhook tests. The MCP suite included its local HTTP transport test.

`make frontend-test` passed 91 tests, TypeScript checks, ESLint, and the production build.

The combined live Chromium run passed all 15 workflows in 39 seconds. The backup creation and restore workflow also passed in the preceding GUI pass.

The live Chromium checks cover:

| Area | Verified actions |
| --- | --- |
| Navigation | Main pages, mobile menu, dialog focus and Escape, failed project list and Retry, global search, grid/list switching, unknown run tabs, missing routes, expired sessions |
| Projects and imports | Create project, local single and batch upload, lost upload response retry without duplicate sources, CSV export, move run, empty and populated experiment deletion, active-job deletion protection and cancellation recovery |
| Spectra | Run tabs, source download, build persistent catalog, render centroid spectrum |
| SDRF | Save project, generate table, edit headers and cells, save, validate current edits, export content, add and remove rows and columns, failed import and same-file retry, repository ZIP download with source bytes and SHA-256 verification |
| Processing | Create profile, preset format restriction, metadata-only rule, disable rule, clear selections, failed preview recovery, queue batch, cancel queued jobs, retry failed and cancelled batches, Activity queued-job cancellation |
| Accounts | Create viewer, disable and enable, create and revoke API token, add and remove project membership, password mismatch and successful change, sign-out during network failure |
| Permissions | Administrator route redirects, unauthorized settings fallback, permitted viewer project access, disabled viewer metadata and processing controls, source download availability |
| Agents and integrations | Register, copy enrollment, disable and enable, save destination, rotate and copy token, copy API URL, documentation endpoints, clipboard denial feedback, webhook create/copy/toggle/delete and failed-update recovery |
| Storage | Reclaim preview failure and retry, zero-file protection, cancellation, locked pending previews, derivative reclamation with source preservation |
| Backups | Save policy, create verified snapshot, explicit isolated restore check, unsaved policy retention through failed status polling |

Additional unit tests reproduce stale processing responses, all-profile deselection, metadata-only automation, advanced filter preservation, structured API errors, and download session expiry. Existing tests cover spectrum filters, pagination, import recovery, PRIDE selection and SDRF preview, download settings, and backup controls.

## Reproducing browser checks

Use a disposable installation. These tests create accounts, projects, jobs, and agent credentials, change settings, and delete test experiments. The backup directory must be outside the database and storage directories. Start an extractor and spectrum reader for the live spectra checks. Leave the webhook delivery worker stopped. The extended suite creates temporary webhook records with a reserved invalid hostname and removes them without sending deliveries. It also uses Python 3 to inspect exported ZIP checksums.

From `frontend`, with the test API running:

```sh
SPECTARR_E2E_GUI=true SPECTARR_E2E_URL=http://127.0.0.1:8339 npx playwright test e2e/gui-hardening.spec.ts e2e/gui-extended.spec.ts e2e/release-smoke.spec.ts --workers=1
SPECTARR_E2E_BACKUPS=true SPECTARR_E2E_URL=http://127.0.0.1:8339 npx playwright test e2e/backups.spec.ts
```

Set `SPECTARR_E2E_USERNAME` and `SPECTARR_E2E_PASSWORD` to override the release-test credentials. The backup test expects a fresh backup history.

## Boundaries

This is workflow regression coverage, not an exhaustive guarantee for every possible server state. This pass did not send external webhook deliveries, connect physical instrument agents, or exercise an external MCP client. It queued and cancelled conversions without running a converter worker. Actual PRIDE downloads and RAW conversion were verified in the preceding PRIDE review, documented in `pride-import-review.md`. The current pass did not repeat those large downloads. Ontology network validation was not exercised. Repository package generation, contents, and checksums were verified in the extended browser pass.
