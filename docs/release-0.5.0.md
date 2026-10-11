# Spectarr 0.5.0 candidate

Validated October 11, 2026. This candidate completes the installation and branding work, adds explicit native conversion, and refreshes compatible dependencies. Sagearr remains separate and unchanged.

## Completed work

- Consistent Spectarr labels, a shorter introduction, screenshots, and installation and first-import guides.
- Independent MCP loopback binding, exact HTTPS origin configuration, persistent generated secrets, and missing-socket detection.
- Optional native executable mount with capability checks, strict recipe validation, preservation policies, cancellation, and source and output hashes.
- Native 64-bit intensity defaults survive creation, updates, and queued snapshots. The dashboard identifies native profiles and prevents the ProteoWizard editor from discarding native policies.
- mzMLPy 0.10.1 and Spxtacular 0.9.2 are pinned by source revision. Backend, dashboard, compatible service dependencies, runtime image digests, and the setup-uv action are updated.
- The backend uses uv-aware Dependabot updates. Production constraints match the backend lock.
- Version surfaces, release notes, the acquisition-agent wheel, and the optional native Compose file are prepared for 0.5.0.

## Verification

| Check | Result |
| --- | --- |
| Backend | 249 passed, 89.08% coverage, Ruff and configured mypy checks passed |
| Dashboard | 132 passed in the full suite, type checks, lint, and production build passed |
| Additional dashboard regression | 25 focused API and automation checks passed, including the added converter-identity check |
| Acquisition agent | 43 passed, one platform-specific check skipped |
| Converter | 46 passed, including all five optional real native-binary checks |
| Extractor, MCP, webhooks | 35, 32, and 20 passed respectively |
| Version and dependency consistency | Passed |
| Release Compose and optional native override | Validated with actual Docker Compose interpolation |
| Initial container integration | Simulated acquisition uploads, interruption and restart recovery, external inventory, native and ProteoWizard conversion, checksum verification, MCP, webhooks, backup and restore passed |
| Initial browser integration | 11 passed, including managed backup settings, 15 opt-in checks skipped |
| Final image release rehearsal | Fresh installation through release Compose, both conversion backends, provenance, downloads, maintenance, concurrent ingestion, restart, independent backup verification and restore passed |
| Final image browser checks | 10 passed, 16 opt-in checks skipped |
| Release packaging | Acquisition-agent wheel and local archive built, archive contents and SHA-256 verified |

The full dashboard run preceded one additional test-only change. The focused run includes that new test and the existing client and automation tests. No application code changed after the final image build.

The initial integration image was `sha256:fd904761c1ca5a29c3db0fbedf46ff187ddd08e131e7203ea291cdc7dc627d6e`. The final candidate adds the native-profile editor protection and the available liblzma5 security update:

```text
spectarr:0.5.0-candidate
sha256:026d4689a3837d418cee4e48256eadac36bd0a64a5ba8a4e1300ce8e951e3cee
```

The initial sandboxed backend attempt was interrupted after fixture timeouts. The complete backend result above comes from the unrestricted local test run.

## Security evidence

The dashboard audit reported zero vulnerabilities. The final image Python audit reported no known vulnerabilities in the audited distributions. Local Spectarr and msconvert-cli source packages are excluded from the PyPI advisory lookup.

The final image scan reported 61 low, 58 medium, 44 high, and one unknown-severity finding, with no available fixes reported and no critical findings. The two fixable liblzma5 findings from the first image were resolved. This meets the existing image audit gate. It does not establish that the remaining findings are harmless. The ProteoWizard vendor image was not rescanned in this pass and retains its previously documented limitations.

Exact image identity, dependency pins, native executable identity, and scan summary are in [acceptance-0.5.0.json](acceptance-0.5.0.json).

## Publication gates

The candidate has not been published or deployed to an existing installation. Before public promotion, run the existing hosted release workflow against the final source revision, including Windows installer testing and the exact candidate image rehearsal. Repeat the full real-vendor qualification matrix on an authorized host. The local native checks and simulated acquisition tests do not qualify every vendor mode or instrument.

The opt-in extended GUI and live PRIDE download suites were not enabled. The final release rehearsal uses isolated local data and does not access an existing library.
