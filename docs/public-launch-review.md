# Public launch preparation

Reviewed September 27, 2026. This prepares the repository for a Spectarr community announcement. No release, tag, or social post was published during this pass.

October 11 follow-up: the completed 0.5.0 candidate is recorded in [release-0.5.0.md](release-0.5.0.md). The verification below describes the earlier September review.

## Prepared

- A short README with the problem, core features, real application screenshots, and a three-command Compose start.
- A documentation index with separate installation, first-run, storage, and development guides.
- Instructions for accessing a remote server through SSH, configuring HTTPS, changing storage paths, updating, and troubleshooting.
- A community announcement draft in `release/community-post.md` and next-release notes in `release/next-notes.md`.
- Spectarr branding in the public introduction, dashboard, browser title, CSV download name, and API display name. Package identifiers and data paths stay compatible.

## Review findings fixed

| Finding | Change |
| --- | --- |
| Publishing the dashboard also published MCP on the same network interface | `SPECTARR_MCP_BIND_ADDRESS` defaults independently to loopback in both Compose files. |
| An HTTPS reverse proxy could fail browser-origin validation with no Compose setting to configure it | Both Compose files accept an exact `SPECTARR_CORS_ORIGINS` list. The guide configures it alongside trusted hosts. |
| A missing Docker socket path could be silently created as a directory | Use a bind mount with `create_host_path: false`. |
| Copying the development environment example disabled automatic secret generation with placeholders | Both examples leave application and worker secrets blank. |
| `make release-check` required an environment file despite the no-configuration installation path | Validate with an empty environment file when no release environment file exists. |
| The README said the project was preparing its first public release | Confirmed the public 0.4.0 release and its Compose asset, then replaced the stale claim. |

The review focused on installation, runtime secrets, authentication bootstrap, host and origin handling, deployment configuration, and the release workflow. It was not a complete security audit.

## Verification

- Backend: 222 tests passed, with 89.14% coverage. Backend lint and configured type checks passed.
- Dashboard: 131 tests passed, plus type checks, lint, and a production build after the Spectarr label changes. The build retains its existing large-bundle advisory.
- Deployment: 10 new checks passed against actual Docker Compose interpolation for both deployment files. They cover private defaults, dashboard-only network exposure, proxy origins, rootless socket configuration, and both environment examples.
- After the API display-name change, all 33 focused runtime, browser-security, and deployment checks passed.
- Service suites: 43 agent, 41 converter, 35 extractor, 32 MCP, and 20 webhook tests passed. One platform-specific agent check and five optional native-converter integration checks were skipped.
- Version consistency, production dependency consistency, and `make release-check` passed.
- A clean temporary installation booted with the changed release Compose file and the published 0.4.0 image, without an environment file or manually configured secrets.
- Browser setup created the first administrator. Both existing browser release workflows passed, covering import, indexed spectra, batch upload, and a lost upload-response retry without duplicate sources.
- Container replacement preserved the administrator, generated secrets, and all three source-file checksums. Authentication and downloads worked afterward.
- Exact HTTPS proxy origins were accepted. Untrusted hosts and origins were rejected with HTTP 403.
- The built Spectarr dashboard was separately installed in that disposable container for the final browser workflow checks.

The published image used for the installation checks resolved to `ghcr.io/pgarrett-scripps/spectarr@sha256:f1f8ff267a460953e8425bd69afd698c6473abb6ba6e84443b14d36186ada019`. Those checks qualify the setup changes against that backend. They do not qualify a newly built release image containing every working-tree change.

Screenshots use the existing demo library through the current frontend. No acquisition or API result was fabricated for the screenshots. See [capture notes](assets/README.md).

## Before announcing

1. Review the pre-existing native-conversion work separately. It was preserved. Its optional binary integration tests were not enabled in this pass.
2. Choose the next release version and finalize the prepared `release/next-notes.md` as `release/notes.md`. The latter still describes the already published 0.4.0 release.
3. Run the existing hosted release gates against the final source revision, including Windows packaging and the clean-image rehearsal. This pass did not repeat the full vendor matrix, backup restoration, or image vulnerability scan.
4. Publish the tested image and updated standalone Compose asset together. The existing 0.4.0 release asset does not yet contain these new Compose settings, and its dashboard still uses the previous display name.
5. Verify the README's latest-release download from a fresh directory, then use the announcement draft. Check the destination community's current rules before posting.

The vendor-image security limitations recorded in the existing release notes remain relevant. This pass does not claim to resolve or rescan them. Docker socket access remains a privileged capability and is disclosed in the installation guide and announcement draft.
