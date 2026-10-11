# Community announcement draft

Draft only. Publish after the launch checks in `docs/public-launch-review.md` are complete. Check the community's current posting rules and disclose that you build the project.

## Suggested title

I built Spectarr: a self-hosted library for mass spectrometry data, with a browser spectrum viewer

## Post

Hi everyone, I'm building Spectarr, an open-source app for a fairly niche self-hosting problem: keeping mass spectrometry data organized on your own server.

In a lab, original instrument files can end up spread across acquisition computers, shared drives, and processing folders. I wanted one place to find a run, see which project and sample it belongs to, inspect its spectra, and trace how its processed files were made.

Spectarr gives you:

- Projects, experiments, samples, and searchable runs
- A browser spectrum viewer with scan filters and chromatograms
- Checksummed originals and conversion to mzML, mzXML, MGF, and MS2
- Browser uploads, public PRIDE dataset downloads, and a Windows or Linux acquisition agent
- A readable file library, REST API, and read-only MCP tools

The app uses Docker Compose, SQLite, and filesystem storage. There is no separate database server to configure. It targets Linux x86-64. Vendor conversion starts separate ProteoWizard containers and needs access to the Docker socket, which gives it control of that daemon. The README links to deployment and backup details.

It's early-stage, and support varies by vendor and acquisition mode. I'm especially interested in feedback on installation, everyday organization, and what researchers or core facilities would need before trying it with their own instruments.

Code, screenshots, and setup: https://github.com/pgarrett-scripps/spectarr

License: Apache 2.0.

## Images to attach

Use `docs/assets/spectarr-release-spectra.png` first and `docs/assets/spectarr-release-project.png` second. Both show the running application with demonstration acquisitions. Keep the post concise and let the README carry the setup instructions.
