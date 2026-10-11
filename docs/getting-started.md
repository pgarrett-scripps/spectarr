# Your first run

[Install Spectarr](../release/README.md), open the dashboard, and create the first administrator account. Choose a password of at least 12 characters. This account is local to your server.

## Try the included sample

1. Download [demo.mgf](https://raw.githubusercontent.com/pgarrett-scripps/spectarr/main/examples/demo.mgf) to your computer. The release archive also includes it in `examples/`. It contains two small synthetic spectra for exploring the interface.
2. Choose **Import data** from the overview.
3. Enter a project such as `My first project` and an experiment such as `Demo`. Select `demo.mgf` under **Source files**.
4. Review the run and sample names, then choose **Import 1 run**.
5. Follow the link to the imported run. Open **Spectra** after metadata extraction finishes. Choose a spectrum row to inspect its peaks.
6. Open **Files** to download the original or locate its managed path. **Processing** and **Provenance** show what happened to the run.

Importing a small open-format fixture checks the basic workflow. It does not qualify vendor conversion or instrument collection.

## Bring your own data

Use browser uploads for individual files or batches. For files already on the server, place them in the installation's `imports` directory and use the server-path import option with a path under `/imports`. The mount is read-only, and managed import copies the source into the library.

Use [PRIDE import](online-dataset-import.md) to download a public dataset directly to the server. Use [external inventory](external-inventory.md) to catalog an existing archive without copying every file. Use the [acquisition agent](../services/agent/README.md) when you are ready to collect new files from an instrument computer.

## Before storing important data

Configure a separate backup destination and enable **Settings → Backups** using the [backup guide](backup-integration.md). Verify a backup and rehearse restoring it. If other people will connect, follow the [HTTPS setup](../release/README.md#https-access-for-a-team) and keep password authentication enabled.
