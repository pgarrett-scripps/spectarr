# Changelog

All notable changes to Spectarr will be documented in this file. Releases follow Semantic Versioning.

## Unreleased

### Added

- Release-readiness validation, clean-room rehearsal tooling, and production smoke coverage
- Public conversion-library contracts for reproducible ProteoWizard execution
- Security reporting and release governance documentation
- Signed Windows acquisition-agent MSI packaging with native service registration, rotating logs, and lifecycle scripts
- Dashboard controls for agent disablement and one-time token rotation

### Changed

- Running processing jobs now honor batch cancellation through the converter worker
- Version consistency is validated against the canonical `VERSION` file
- Acquisition identity now preserves distinct occurrences with identical content while retaining checksum-based storage deduplication

## 0.1.0

Initial release candidate for the self-hosted mass spectrometry library, processing, extraction, acquisition, webhook, dashboard, REST, and MCP services.
