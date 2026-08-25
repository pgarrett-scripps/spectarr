# Contributing

Spectarr is organized as a small set of independently testable services.

- `backend` owns the data model, ingest, artifact storage, and REST API
- `frontend` owns the browser dashboard
- `services` owns conversion and MCP adapters

Keep scientific source artifacts immutable. New transformations must create derived artifacts with recorded input checksums, recipe parameters, and tool versions.

Before submitting a change, run:

```bash
make test
```
