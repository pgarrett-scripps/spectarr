# Spectarr 0.5.0

Spectarr 0.5.0 simplifies installation, adds an optional native conversion backend, and updates its reader and application dependencies.

## New and improved

- Clearer setup, first-import, HTTPS, storage, update, and troubleshooting guides, with screenshots from the application.
- Consistent Spectarr branding across the dashboard and public documentation.
- Independent loopback binding for MCP, exact reverse-proxy origin configuration, automatic persistent secrets, and early failure for missing Docker sockets.
- Explicit `msconvert-rs` recipes with capability checks, preservation policies, resource limits, cancellation, source and output verification, and executable hashes in provenance.
- Native recipes default to 64-bit arrays. Unsupported settings fail during recipe creation or update.
- Optional `compose.native.yaml` mounts a compatible administrator-supplied native executable read-only. ProteoWizard remains the default converter.

## Dependency updates

mzMLPy 0.10.1 improves activation metadata, possible precursor charges, chromatogram recognition, and unsupported imzML handling. Spxtacular 0.9.2 corrects MS2 output, native-ID preservation, isotope envelopes, and fragment annotation.

Compatible Python and dashboard dependencies are refreshed, including FastAPI 0.142.4. The source-map-js audit finding is fixed. The Node 26, Python 3.14, Docker 29 CLI, and .NET 10 base images use refreshed immutable digests. Backend dependency automation now uses uv so manifest and lockfile updates travel together.

## Upgrading

Back up and verify the installation before updating. This release adds no database migration. Image updates do not replace your Compose file, so merge the new deployment configuration while retaining your storage paths and secrets.

MCP defaults to loopback independently of dashboard access. Configure `SPECTARR_MCP_BIND_ADDRESS` explicitly if you intentionally expose it. Configure exact trusted hosts and HTTPS origins for reverse proxies.

The native executable is optional and is not bundled in the application image. Vendor and acquisition-mode qualification remains limited. Unsupported operations fail without changing to another converter automatically.

## Operational limits

Linux x86-64 with Docker Engine and Compose v2 remains the tested deployment target. Docker socket access is privileged. The ProteoWizard vendor image retains the security limitations described in the 0.4.0 release. Updating the application does not resolve those vendor-image findings. The application image audit reports no fixable vulnerabilities, but retains OS findings without available fixes. See the candidate acceptance record for the scan scope and exact image.

Instrument completion detection remains a heuristic. Validate actual acquisition behavior before unattended collection. The Windows acquisition-agent installer is unsigned. Verify its attached SHA-256 checksum before installation.

Release promotion remains gated on the hosted Linux, Windows packaging, and exact-image rehearsal checks.
