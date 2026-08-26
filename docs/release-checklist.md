# Release checklist

## Automated gate

Every release tag must pass these jobs in order:

1. Validate that the tag and every package surface match `VERSION`.
2. Build all six Linux x86-64 images with SBOM and provenance metadata.
3. Test the acquisition agent on Windows and build its standalone executable.
4. Authenticode-sign the agent executable and MSI, then produce the installer checksum.
5. Pull the versioned images into a clean temporary installation.
6. Start PostgreSQL, API, dashboard, converter, extractor, webhook, and MCP services.
7. Bootstrap an administrator through the dashboard HTTP boundary.
8. Create a project, experiment, sample, and run.
9. Upload the bundled MGF fixture and verify its recorded checksum.
10. Download the artifact through the authenticated API and verify the checksum again.
11. Wait for automatic metadata extraction.
12. Create, verify, and restore a database and artifact backup into separate targets.
13. Build the release archive and checksums only after the rehearsal succeeds.

Tagged releases require `WINDOWS_SIGNING_CERTIFICATE_BASE64` and `WINDOWS_SIGNING_CERTIFICATE_PASSWORD` repository secrets. The first contains a base64-encoded PFX code-signing certificate. The Windows job refuses to publish a tagged release without both values.

Run the same rehearsal against already published images from a Docker-capable workstation:

```bash
scripts/release-rehearsal.sh
```

Set `SPECTARR_KEEP_REHEARSAL=true` to retain the temporary installation for inspection after the command exits.

## Vendor conversion acceptance

The portable gate does not download proprietary vendor fixtures or accept ProteoWizard licenses on behalf of a user. Before promoting a release candidate to stable, run this matrix on an authorized x86-64 host:

1. Thermo RAW file conversion to mzML, mzXML, MGF, and MS2
2. One large vendor RAW file that exercises the OpenMassSpec fallback path
3. One atomic Bruker `.d` directory acquisition
4. Cancellation of a running conversion, followed by retry
5. Checksum verification after authenticated download
6. Storage reclamation and regeneration of one derivative

Record fixture names, tool and image versions, output checksums, spectrum counts, and any parser warnings in `docs/acceptance-testing.md`.

## Manual operational review

- Confirm the release archive includes Compose configuration, environment template, acquisition-agent wheel, backup tools, security policy, changelog, license, and smoke fixture.
- Confirm the Windows MSI and its checksum are attached and both the MSI and embedded agent executable have valid Authenticode signatures.
- Confirm production ports bind to loopback unless remote exposure was explicitly configured.
- Confirm application, worker, and database secrets are independent and strong.
- Confirm the converter uses a rootless Docker-compatible socket when available.
- Confirm a restored database and storage tree can be started independently of the active installation.
- Confirm the GitHub release checksum and image digests match the tested artifacts.
