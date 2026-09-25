# MassSpec 0.4.0

MassSpec 0.4.0 adds a calmer research interface, public dataset imports, and optional read-only discovery of acquisitions stored outside the managed library. Existing Spectarr package names, configuration keys, and storage paths remain compatible.

## New and improved

- Browse projects and runs in a focused academic interface with clearer processing status, responsive tables, and improved keyboard navigation.
- Find public PRIDE datasets, select acquisitions, and manage resumable downloads with configurable concurrency, cancellation, retry, and provenance. Preview and import available SDRF metadata.
- Register explicit external folders for read-only inventory. Preserve content revisions and location history, verify relocations, and import selected acquisitions into managed storage when required.
- Let agents locate managed and external files through read-only MCP tools, with project permissions and host-scoped paths.
- Preserve native Thermo and Bruker spectrum identities and peak arrays. Support Spxtacular 0.9 and mzMLPy 0.10 schemas and compressed-file access.

## Reliability and dependencies

Python 3.14, SQLAlchemy 2.1, TypeScript 7, Vitest 5, Node 26, and .NET 10 replace the older runtime and development stack. ProteoWizard msconvert 3.0.26266 is pinned by immutable image digest. Upstream compatibility requirements remain respected, including the TypeScript API bridge used by ESLint.

Browser request checks, webhook DNS handling, administrator bootstrap, XML parsing, and converter isolation are hardened. Backup operations now explicitly close SQLite connections. Processing, uploads, maintenance, and polling recover more reliably from interruptions.

Local qualification passed 506 component tests, 10 browser checks, native vendor reader checks, simulated instrument uploads and conversions, read-only discovery scenarios, and independent backup restoration. Hosted release gates separately check Linux integration, packaging, the Windows installer, and the release image before promotion.

## Upgrade and operational notes

Back up and verify the existing installation before upgrading. Database migrations add repository-download and external-inventory records. To roll back to an earlier release, restore the matching backup rather than assuming database downgrade support.

Read-only inventory is optional and does not back up external files. An offline or moved location remains recorded with its availability state. Managed imports retain immutable source acquisitions.

Instrument completion detection remains a heuristic. Files paused longer than the stability window may look complete without an explicit producer marker. Validate actual instrument write behavior before unattended collection. Linux simulation does not qualify a physical instrument or every Windows and network-share configuration.

## Known security limitations

The main image scan has no critical or fixable findings, but retains reported OS vulnerabilities without available fixes. The latest upstream ProteoWizard image still uses Ubuntu 20.04 and includes bundled Java components with reported critical and high findings. The unsupported OS also limits scanner coverage. Conversion jobs run with networking disabled, dropped capabilities, no new privileges, and read-only inputs. These controls do not establish that the remaining findings are harmless. Docker socket access remains a privileged capability.

See the bundled dependency migration report for versions, evidence, compatibility exceptions, and the full limitations. The release does not claim to eliminate all security findings.

## Installation

Use the release archive for Compose configuration, the acquisition-agent wheel, backup and restore scripts, and operating guides. The versioned image is `ghcr.io/pgarrett-scripps/spectarr:0.4.0`. The dashboard remains available at http://localhost:3280/ with default loopback bindings.

The Windows acquisition-agent installer is unsigned. Windows may show an unknown-publisher warning. Verify the attached SHA-256 checksum before installation.
