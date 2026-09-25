# Latest dependency migration, September 25, 2026

The supported application stack is migrated, tested, and running locally at http://localhost:3280/. Nothing was published. This pass completes the major upgrades deferred in the earlier security pass.

| Component | Previous baseline | Qualified version |
| --- | --- | --- |
| Python runtime | 3.12 | 3.14.7 |
| Runtime OS | Debian 12 Bookworm | Debian 13 Trixie |
| Node build runtime | 22 | 26.10.0 |
| .NET native reader runtime | 8 | 10.0.12 |
| SQLAlchemy | 2.0.54 | 2.1.1 |
| TypeScript compiler | 5.7.3 | 7.0.2 |
| Vitest | 4.1.11 | 5.0.2 |
| Mypy | 1.20.2 | 2.3.1 |
| Fastparquet | 2026.5.0 | 2026.9.0 |
| ProteoWizard msconvert | 3.0.26121 | 3.0.26266 |

The frontend's direct dependencies are at their latest stable registry releases, including React 19.3.0, React Router 7.18.4, Vite 8.3.1, Playwright 1.63.0, and ESLint 10.11.0. Every locked backend dependency and development tool was compared with PyPI. The only backend exception is the exact Pydantic Core version required by Pydantic, described below.

The scientific stack remains on the latest releases established in the previous pass: Spxtacular 0.9.0, mzMLPy 0.10.0, tdfpy 5.0.0, Peptacular 5.0.0, Paftacular 2.0.0, Tacular 2.0.0, Fisher-py 2.0.2, and OpenMassSpec and OpenMassSpec IO 1.5.5. Docker CLI 29 and the external CI action pins were already current. Runtime images and the vendor converter use immutable digests.

## Compatibility choices

TypeScript 7 compiles and type-checks the dashboard. ESLint uses the official TypeScript 6 compatibility API under an npm alias, following [Microsoft's migration guidance](https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/). The compatibility package is 6.0.2 and exposes the legacy API without replacing the native `tsc` binary. No forced peer dependency resolution is used.

Pydantic 2.13.5 requires Pydantic Core 2.46.5 exactly. Installing the independently newer Core 2.49.0 would violate that dependency contract. The [published package metadata](https://pypi.org/pypi/pydantic/2.13.5/json) records the requirement.

Some npm dependencies internally require older major versions, including Babel 7 and older utility packages. Those constraints remain owned by their upstream maintainers. `npm update` reports the supported graph is up to date, direct `npm outdated` returns no entries, `npm ls` accepts the graph, and npm audit reports zero findings. The retained transitive report lists 65 package names, including the compatibility alias. Optional packages that are not installed are excluded from that list. We do not claim every transitive library is on its newest independent major release.

The local msconvert-cli source remains at 1.2.0 with its existing corrections. Its published PyPI version is only 1.1.0, so the local source was preserved.

ProteoWizard's floating `latest` tag still points to its May build. The newest stable image published on September 23 includes msconvert 3.0.26266. The default now pins `skyline_26.1.0.266-578b175` with digest `sha256:448a833eb92eac2ca731f2461c046963c0c2afafb3550b4dfb6fc3914cec7b0e`. Both the [official image tag registry](https://hub.docker.com/r/proteowizard/pwiz-skyline-i-agree-to-the-vendor-licenses/tags) and the binary's own version output were checked. Daily builds were not selected.

## Migration corrections

The Python upgrade exposed SQLite connections left open by backup and verification operations. These operations now close their connections explicitly on successful and failing paths. A regression test verifies both. The final backend run emits no warnings. Deprecated Starlette status constant names were replaced without changing response codes.

CI and local service tests now select Python 3.14. Docker and CI build the dashboard on Node 26. Local version files document those runtime choices. Build contexts exclude coverage and linter caches.

The instrument rehearsal now converts every supplied source format, including native vendor fixtures. It independently counts converted mzML spectra and declared peaks, then checks catalog identities and retrieved arrays. Sparse TimSim output can contain empty first and last spectra, so sampling also includes spectra with the largest peak counts. Bruker source records and converted spectra remain separate observations because the readers group ion mobility scans differently.

## Validation

- 215 backend tests passed with 88.96 percent coverage. Ruff and Mypy passed.
- 131 frontend tests passed on Node 26.10.0 and Vitest 5. TypeScript 7 checks, lint, and the production build passed.
- 160 service tests passed on Python 3.14.7: 43 agent, 30 converter, 35 extractor, 32 MCP, and 20 webhook tests.
- All 10 browser checks passed against the final image, including password login, batch uploads, spectrum viewing, keyboard behavior, and external inventory.
- Simulated acquisition tests passed with partial writes, completion markers, lost successful responses, outages, hard agent restarts, server restarts, and deduplication.
- Read-only discovery checks passed for inventory, MCP lookup, relocation history, explicit imports, same-size edits, unreadable roots, and restarts.
- Conversion and independent XML checks passed for MGF (1,200 spectra and 7,200 peaks), Thermo (10 spectra and 26,390 peaks), and TimSim Bruker (63,074 spectra and 310,145 peaks).
- Direct Spxtacular reading inside the final image returned the reference Bruker counts of 65 MS1 and 2,519 MS2 spectra, and 10 Thermo MS2 spectra. The fixtures were mounted read-only.
- The fresh backup booted independently on the final image and verified 36 managed storage objects.
- The live deployment retained all 20 run IDs, 44 artifact records, and the sizes and SHA-256 hashes of 45 checked files. Database, storage, and container health are good.

A local three-run forced type-check comparison had median times of 4.226 seconds for the TypeScript compatibility compiler and 0.354 seconds for TypeScript 7, about 12 times faster. These are development build measurements on this machine, not a claim about application response times or all workloads.

The acquisition completion heuristic remains a limitation: an unmarked file paused longer than the configured stability window can appear finished. The negative control reproduces that case without uploading the incomplete file. These Linux tests do not qualify a physical instrument, Windows service operation, or network-share behavior.

## Security findings retained

The final main image has zero known Python audit findings, zero npm audit findings, zero critical image findings, and no fixable image findings. It still has 156 reported OS package findings: 44 high, 53 medium, 57 low, and 2 unknown. The earlier Debian 12 image had 255 findings, including five critical findings. Counts are package/advisory occurrences, not counts of distinct vulnerabilities or proven exploit paths.

The latest upstream vendor conversion image remains on Ubuntu 20.04, outside standard support. Its scan reports 103 findings in bundled Java components, including four critical and 42 high findings with upstream fixes. The unsupported OS also limits the completeness of OS vulnerability detection. Updating to the latest vendor image does not resolve this separate security debt. Both the stale floating tag and the September stable image were scanned and their full reports were retained.

Conversion jobs continue to run without network access, with all Linux capabilities dropped, no new privileges, a process limit, and read-only input mounts. These controls do not establish that the remaining findings are harmless. Replacing or rebuilding the upstream vendor runtime requires its own compatibility qualification across supported vendor formats. The application also retains Docker socket authority for launching conversion jobs.

## Local deployment and rollback

The running image is `sha256:e55d7f1cb1577f76a536f94879a040663124cde3d3b34c50ba4ab873331a66cf`, tagged locally as `spectarr-spectarr` and `spectarr-latest:candidate`. API and MCP ports remain bound to loopback.

The previous image is retained as `spectarr:before-latest-20260925`. The pre-upgrade backup is `storage/upgrade-backups/latest-20260925/spectarr-20260925T211600Z`. An independent restored copy is retained at `/tmp/spectarr-latest-final-restore`. No schema migration was introduced by this dependency pass.

The source checkpoint is `storage/local-integration-snapshots/latest-2026-09-25`. Machine-readable version, security, browser, test, backup, and live verification evidence is in `storage/security-validation/latest-2026-09-25`. Disposable test containers were removed. No actual archive roots or instrument watch folders were enrolled.
