# Source-control recovery

The working directory must be connected to its authoritative Git history before a release tag is created. An empty `.git` directory is not a usable repository and must not be treated as a new project without first locating the intended remote.

## Safe recovery sequence

1. Identify the authoritative Spectarr remote URL and expected default branch.
2. Clone that remote into a separate temporary directory.
3. Compare its tracked tree with this workspace, excluding generated data, secrets, virtual environments, dependencies, and build output.
4. Copy or apply the workspace changes onto a new branch in the valid clone.
5. Run `make test` and `scripts/ci-integration.sh` from that branch.
6. Review the complete diff before committing.
7. Push the branch and require CI before merging or tagging.

Do not run `git init` in this workspace unless the project is intentionally starting with no prior history. Initializing over an unknown history can create an unrelated root commit and make later reconciliation harder.
