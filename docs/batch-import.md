# Batch import

Open **Import run** from a project, or **Import data** from the overview. A project opened before import stays selected.

1. Choose the destination project and experiment. Existing names are reused. New names create the destination when importing starts.
2. Select multiple source files. You can select more files to extend the queue. Alternatively, choose server paths, enter one allowlisted path per line, and select **Add paths**. A batch may contain both files and paths.
3. Review the queue. Each acquisition starts with its filename as the run and sample name, excluding recognized format extensions. Edit individual names or apply one sample to every row. A run name pattern supports `{name}` and `{index}`, for example `cohort-{index}-{name}`. Select **Apply run names** to preview the result before importing.
4. Remove unwanted sources, then select **Import N runs**.
5. Follow each item through preparation, upload, and source registration. Failed items retain their error message while the rest of the queue continues. Use an individual **Retry** button or **Retry failed** after correcting the underlying connection, storage, or permissions problem.
6. Open an imported run or select **View project runs** when the batch finishes.

Imports run one at a time to limit memory, disk, and network contention. Upload percentages describe bytes transferred. Registration can continue after the transfer reaches 100 percent. An imported source may still have metadata extraction or conversion running, which is shown on its run page.

Project, experiment, run, and sample assignments are fixed once a batch starts so retries use the same request. The queue belongs to the current page. Keep it open until importing finishes. Closing or reloading the page discards its files, progress, and retry keys. Browser uploads restart the file transfer on retry. Use the acquisition agent when uploads must resume across application restarts or long network outages.

Browser selection accepts files. For vendor acquisitions stored as directories, such as Bruker `.d`, use the directory's server path or the acquisition agent. A server path refers to the server's configured import roots, not a path on the browser's computer. Spectarr imports the whole directory as one acquisition.

## Retry behavior for API clients

Run creation, artifact upload, and server-path import accept an optional UUID in the `Idempotency-Key` header. The browser generates one key per queue item and retains it for retries. The server scopes generated record IDs to the authenticated actor, operation, and run where applicable.

Repeating a compatible request with the same key returns the existing run or artifact. Changed run assignments or conflicting artifact contents return HTTP 409. Artifact retries compare the ingested content checksum and reconcile automatic processing, including when the original success response was lost. A fresh key intentionally creates a separate acquisition. Existing API clients that omit the header retain the previous behavior.

## Validation

The implementation passed 107 backend tests with 87.05% coverage, 64 dashboard tests, lint, type checks, and a production build. The isolated container rehearsal passed ingestion, concurrent restart recovery, backup, and restore checks. Both browser workflows passed, including a batch that loses a successful upload response, continues with the next item, and retries without duplicate runs or sources.
