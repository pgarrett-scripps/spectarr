# Dashboard interaction contract

The dashboard must not present a state-changing control unless it calls a real API operation. Client-only controls are permitted only when their complete effect is local and immediately visible, such as filtering a table or copying text.

## Server-backed controls

| Dashboard control | Server operation |
| --- | --- |
| Create project | `POST /api/v1/projects` |
| Add project member | `POST /api/v1/projects/{project_id}/memberships` |
| Remove project member | `DELETE /api/v1/projects/{project_id}/memberships/{membership_id}` |
| Import run by upload | Project, experiment, sample, and run creation followed by `POST /api/v1/runs/{run_id}/artifacts/upload` |
| Import run by server path | Project, experiment, sample, and run creation followed by `POST /api/v1/runs/{run_id}/artifacts/import` |
| Download artifact | `GET /api/v1/artifacts/{artifact_id}/download` |
| Generate mzML, mzXML, MGF, or MS2 | `POST /api/v1/runs/{run_id}/derivatives` |
| Extract or re-extract metadata | `POST /api/v1/artifacts/{artifact_id}/extract` |
| Retry failed job | `POST /api/v1/jobs/{job_id}/retry` |
| Register instrument agent | `POST /api/v1/agents/register` |
| Change agent upload destination | `PATCH /api/v1/agents/{agent_id}` |
| Load instrument inbox | `GET /api/v1/inbox` |
| Move one or many runs | `POST /api/v1/runs/bulk-assignment` |
| Create automation rule | `POST /api/v1/automation-rules` |
| Enable or disable automation rule | `PATCH /api/v1/automation-rules/{rule_id}` |
| Create user | `POST /api/v1/users` |
| Change user role or state | `PATCH /api/v1/users/{user_id}` |
| Sign out and revoke browser session | `POST /api/v1/auth/logout` |
| Change password and revoke other sessions | `POST /api/v1/auth/password` |
| Create API token | `POST /api/v1/tokens` |
| Revoke API token | `DELETE /api/v1/tokens/{token_id}` |
| Save project submission metadata | `PATCH /api/v1/projects/{project_id}` |
| Generate project SDRF | `POST /api/v1/projects/{project_id}/sdrf/generate` |
| Import project SDRF | `POST /api/v1/projects/{project_id}/sdrf/import` |
| Save ordered SDRF table | `PUT /api/v1/projects/{project_id}/sdrf` |
| Validate SDRF | `POST /api/v1/projects/{project_id}/sdrf/validate` |
| Export SDRF TSV | `GET /api/v1/projects/{project_id}/sdrf/export` |
| Download repository package | `GET /api/v1/projects/{project_id}/submission/export` |
| Create webhook | `POST /api/v1/webhooks` |
| Enable or disable webhook | `PATCH /api/v1/webhooks/{webhook_id}` |
| Delete webhook | `DELETE /api/v1/webhooks/{webhook_id}` |
| Preview experiment deletion | `GET /api/v1/experiments/{experiment_id}/deletion-preview` |
| Delete experiment | `DELETE /api/v1/experiments/{experiment_id}` with typed confirmation |
| Preview reclaimable derivatives | `POST /api/v1/storage/reclaim/preview` |
| Clear regenerable derivatives | `POST /api/v1/storage/reclaim` with typed confirmation |

## Client-only controls

These controls have complete effects in the browser and do not claim to change server state:

- Route navigation and modal open or close controls
- Grid and list presentation switching
- Run table filtering and project filter clearing
- Activity view filtering
- CSV generation from the currently loaded run list
- Clipboard copy controls
- Refresh controls that repeat the associated API request

## Prohibited patterns

- Representative or demo data after an API failure
- Static disabled settings presented as future functionality
- Success or health badges without a live health source
- Buttons whose only effect is visual feedback for an operation that never reached the server
- Silent API error fallbacks

The frontend interaction tests enforce the structural rules. Backend and client tests verify the listed API operations.
