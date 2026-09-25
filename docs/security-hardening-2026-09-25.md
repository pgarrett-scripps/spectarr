# Local dependency and security review

The subsequent [latest dependency migration](latest-dependency-migration-2026-09-25.md) supersedes the runtime versions and deferred major upgrades recorded below. This document preserves the earlier hardening baseline.

Date: 2026-09-25. Scope: Spectarr, its acquisition and processing services, the frontend, build dependencies, and the local application image. Nothing was pushed or published.

## Dependency changes

| Component | Previous | Updated |
| --- | --- | --- |
| Spxtacular | 0.7.0 | 0.9.0 |
| mzMLPy | 0.9.0 | 0.10.0 |
| tdfpy | 4.0.1 | 5.0.0 |
| Peptacular | 3.3.0 | 5.0.0 |
| Paftacular | 1.2.0 | 2.0.0 |
| Tacular | 1.1.0 | 2.0.0 |
| OpenMassSpec and its IO package | 1.5.4 | 1.5.5 |
| rapidgzip | absent | 0.16.0 |
| SQLAlchemy | 2.0.52 | 2.0.54 |
| Starlette | 1.6.0 | 1.7.0 |
| AnyIO | 4.14.2 | 4.15.1 |
| Uvicorn | 0.52.4 | 0.54.0 |
| React and React DOM | 19.2.8 | 19.3.0 |
| React Router | 7.18.2 | 7.18.4 |
| Vite | 8.2.2 | 8.3.1 |
| Playwright | 1.62.1 | 1.63.0 |
| Docker CLI image line | 27 | 29 |

Compatible transitive dependencies and development tools were refreshed in the backend and frontend lockfiles. The production constraints match the backend lock. Python 3.12, Node 22, and .NET 8 base images remain on their existing runtime lines with refreshed immutable digests. All external workflow actions now use exact commits.

SQLAlchemy stays on 2.0. The TypeScript 7 and Vitest 5 major migrations were deferred because the current toolchain passes its checks and npm reports no known vulnerabilities. Fisher-py 2.0.2 remains current. The locally corrected msconvert-cli 1.2.0 source and existing ProteoWizard image were preserved. The public msconvert-cli release is older than this local source.

The clean sibling Spxtacular and mzMLPy checkouts were fast-forwarded to their tested release tags. Their exact commits are recorded in DEPENDENCIES.env. Existing msconvert-cli modifications were retained.

## Scientific compatibility

Spectrum JSON schema 2 is accepted alongside schema 1 for existing clients and fixtures. New precursor m/z and mobility fields survive the vendor adapter and display correctly. Catalog summaries use the new precursor field. No stored acquisition or catalog was rewritten.

Ordinary gzip uses rapidgzip instead of mzMLPy 0.10's full-memory fallback. Indexed gzip continues to use its embedded index. A regression fixture checks all three access modes, exact peak arrays, native scan identity, retention-time units, missing precursor intensity, and absence of new sidecars or extracted files next to the source. This is an access-strategy test, not a multi-gigabyte memory benchmark.

Native Thermo and Bruker viewing continues to prefer OpenMassSpec so its identities remain consistent with extraction. Installing tdfpy 5 does not silently switch existing native catalogs to a different reader. Upstream reports centroid differences in tdfpy 5, so numerical identity with the old optional Bruker path is not assumed.

## Fixed security findings

1. The credential-free local API accepted requests without validating browser origin or DNS hostname. Host and origin checks now run before dispatch, including authentication setup. Cross-site requests and opaque origins are rejected. API responses receive no-store caching. The dashboard has a content security policy.
2. HTTP MCP delegated through its configured server identity without browser-origin checks. It now checks host and origin, rejects non-JSON and transfer-encoded requests, bounds request size and socket timeout, and supports a separate bearer token. The standalone listener defaults to loopback.
3. Webhook DNS was checked separately from the subsequent connection. The transport now connects only to the checked numeric address, preserves the original TLS verification hostname, ignores ambient proxy configuration, and never follows redirects. Malformed HTTP responses become retryable failures.
4. Concurrent first-run setup could create multiple administrators. A process-safe lock now serializes the bootstrap check and creation. Concurrent requests are tested to produce one success and one conflict.
5. Converter XML validation accepted entity declarations. DefusedXML now rejects entity expansion in plain and compressed conversion outputs.
6. Conversion containers now run without networking or Linux capabilities, with no-new-privileges and a process limit. Source mounts remain read-only. The application container disables privilege gain and drops NET_RAW while retaining the startup permissions it requires.
7. The old installer and Docker CLI carried known advisories. Builds use updated pip, then remove pip and ensurepip from the final runtime because even current pip bundles older vulnerable libraries. Image updates, rather than in-place package installs, are the supported update path.

## Audit scope and residual findings

The initial npm audit was already clear. The old image's Python audit flagged six distinct pip advisory IDs, with duplicate entries in the service response. Trivy found 80 package/advisory findings with available fixes, including 25 high or critical findings.

The hardened runtime has no known Python-package, Docker CLI, or .NET findings in the retained scan, and no image findings with an available fix. npm audit and pip-audit report no known vulnerabilities for the packages they checked.

This does not mean the image is vulnerability-free. The Debian scan still reports 255 package/advisory findings: 55 high, 5 critical, 100 medium, 94 low, and 1 unknown. They have no fixed version in the scanned distribution metadata. Counts are package/advisory pairs, not unique exploitable application flaws. They are retained without suppression. Critical entries concern libsqlite3, perl-base, and zlib. Status includes affected, fix deferred, and will not fix. No claim of non-exploitability is made.

Further boundaries:

- The Docker socket remains available to the conversion supervisor. Access to it is effectively host-administrator authority. Dropping container capabilities does not neutralize that socket. A separately restricted conversion service is a future architecture task.
- Native parsers still execute inside the application image. This work does not establish safety for hostile vendor binaries or complete resistance to decompression and resource-exhaustion attacks.
- The separate ProteoWizard execution image and the host OS are not included in the application-image vulnerability totals.
- MCP's optional token grants access to its configured identity. It is not per-user authorization. Keep the published endpoint on loopback unless a separately reviewed access boundary is configured.
- pip-audit excludes the local Spectarr distributions and the locally corrected msconvert-cli source because their exact releases are not public registry artifacts. Those sources were reviewed and tested, not certified by the dependency scanner.
- Static analysis findings for intentional container listeners, fixed scratch paths, administrator-configured internal HTTP clients, and a parameterized SQL query were reviewed. XML parser findings were fixed. Static analysis is not a penetration test.

## Configuration and repeatable checks

The default UI remains available at localhost. For a named reverse proxy or workstation DNS alias, add its exact hostname to `SPECTARR_TRUSTED_HOSTS`, expressed as a JSON list. Literal IP addresses are accepted. CORS origin permission does not bypass hostname validation.

Set `SPECTARR_MCP_TOKEN` to require `Authorization: Bearer <token>` on HTTP MCP. `SPECTARR_MCP_PUBLIC_URL` permits the configured named endpoint and its browser origin. The production compose ports remain bound to loopback by default.

Run `python3 scripts/audit-image.py IMAGE NEW_OUTPUT_DIRECTORY` with Docker and uv available. It records the immutable image ID, package inventory, local-source exclusions, pip-audit output, the full Trivy report, and a summary. It fails on Python audit errors or findings and on fixable high or critical image findings. Unfixed findings remain visible in its report. The scanner image and audit tool are pinned. The script needs Docker socket access.

CI now audits npm dependencies and the integration image and retains security reports. Workflow files were checked locally. The external-discovery browser test now signs in when the target uses password mode and was verified in both authentication modes. Hosted CI was not run or published.

## Validation

- 214 backend tests, with 89.08 percent coverage against the existing 85 percent gate.
- 131 frontend tests plus type checking, lint, and production build.
- 43 acquisition-agent, 30 converter, 35 extractor, 32 MCP, and 20 webhook tests. The complete suite passed before the final webhook error-handling addition, and all 20 webhook tests passed after it.
- Eight browser checks for academic layout, responsive navigation, focus, and external discovery. Two additional password-mode browser workflows passed login, import, duplicate prevention, catalog creation, and schema 2 spectrum rendering.
- A first attempt ran the password-only browser cases against local mode. Those two cases correctly failed to find a login form. They passed against a fresh password-mode preview. No application workaround was introduced.
- Acquisition rehearsals use actual Thermo RAW and the retained TimSim-generated Bruker dataset. They cover interrupted uploads, completion markers, duplicate polling, conversion, spectrum retrieval, external relocation, pinned imports, restart, and read-only MCP access.
- Direct Spxtacular readers also passed finite-array and schema checks across 65 Bruker MS1 spectra, 2,519 Bruker MS2 spectra, and 10 Thermo MS2 spectra. These are smoke checks, not equivalence to the preferred native provider.
- Backup restoration verified all 36 managed artifact objects in an independent instance.

Evidence and final deployment identifiers are recorded alongside this report in the machine-readable validation file and under storage/security-validation/2026-09-25.

## Local deployment

The tested image `sha256:f077ae108e50442091ca1a01d72ca79cce2768f7a88517348c98f04a6391f49c` is running at http://localhost:3280/. Database and storage health checks pass. All 20 run IDs, the 44-artifact inventory, and 45 checked file hashes match the pre-update baseline. Both published ports remain bound to 127.0.0.1. The running container confirms no-new-privileges and removal of NET_RAW.

Rollback image: `spectarr:before-hardening-20260925`. Fresh backup: `storage/upgrade-backups/hardening-20260925/spectarr-20260925T204435Z`. The independent restore test used the final image and verified 36 managed objects. Acquisition source hashes and managed-object counts describe different validation sets.

A local source checkpoint, including the existing uncommitted work and updated sibling dependencies, is retained at `storage/local-integration-snapshots/hardening-2026-09-25`. No commits, pushes, remote deployments, or hosted workflow runs were performed.
