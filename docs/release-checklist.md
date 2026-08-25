# Release checklist

## Automated gate

Every release tag must pass these jobs in order:

1. Validate that the tag and every package surface match `VERSION`.
2. Build all six Linux x86-64 images with SBOM and provenance metadata.
3. Pull the versioned images into a clean temporary installation.
4. Start PostgreSQL, API, dashboard, converter, extractor, webhook, and MCP services.
5. Bootstrap an administrator through the dashboard HTTP boundary.
6. Create a project, experiment, sample, and run.
7. Upload the bundled MGF fixture and verify its recorded checksum.
8. Download the artifact through the authenticated API and verify the checksum again.
9. Wait for automatic metadata extraction.
10. Create, verify, and restore a database and artifact backup into separate targets.
11. Build the release archive and checksum only after the rehearsal succeeds.

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
- Confirm production ports bind to loopback unless remote exposure was explicitly configured.
- Confirm application, worker, and database secrets are independent and strong.
- Confirm the converter uses a rootless Docker-compatible socket when available.
- Confirm a restored database and storage tree can be started independently of the active installation.
- Confirm the GitHub release checksum and image digests match the tested artifacts.
