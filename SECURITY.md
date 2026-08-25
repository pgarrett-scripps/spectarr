# Security policy

## Supported versions

Security fixes are provided for the latest published Spectarr release. Pre-release source snapshots are supported on a best-effort basis.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub Security Advisories for the Spectarr repository.

Include the affected version, deployment mode, reproduction steps, impact, and any suggested mitigation. Do not include secrets, credentials, protected scientific data, or proprietary vendor files.

We will acknowledge a report within three business days, provide an initial assessment within seven business days, and coordinate disclosure after a fix is available. Please do not open a public issue until coordinated disclosure is complete.

## Deployment boundary

Spectarr binds published ports to loopback by default. Networked deployments should use HTTPS, strong independent secrets, a rootless Docker-compatible daemon for conversion, scoped API tokens, and a read-only MCP configuration unless writes are explicitly required.
