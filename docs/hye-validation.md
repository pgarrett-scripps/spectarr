# HYE acquisition, conversion and Sage validation

Completed on 2026-09-09 UTC using the current Spectarr source in a separate local instance. The normal app on port 3280 was still version 0.1.0 and did not expose the PRIDE importer. The validation app is on port 8339, with MCP on port 8341. Its storage is `data/hye-validation`, which is excluded from Git.

The workflow passed after correcting compressed output discovery, RAW precursor extraction and the Sage centroiding preset. Sage required a plain mzML copy because the installed build does not read Spectarr's multi-member indexed gzip correctly.

## Dataset and reference database

Selected `LFQ_Orbitrap_DDA_Condition_A_Sample_Alpha_01.raw` from [PRIDE PXD028735](https://www.ebi.ac.uk/pride/archive/projects/PXD028735), following the [ProteoBench Q Exactive benchmark](https://github.com/Proteobench/ProteoBench/blob/main/docs/modules/dda/dda-ion-qexactive.md). This is a complete 150-minute acquisition, not a truncated RAW file. One acquisition was used for functional validation.

The selected SDRF rows describe a mixture of 65% human, 30% yeast and 5% E. coli. Spectarr imported the three rows and linked three samples to the run. The upstream SDRF still references PXD010000 in its accession and file URI columns. Those original values were preserved, while download provenance records the actual PXD028735 source.

The imported SDRF fails current validation because it lacks `technology type`, `comment[proteomics data acquisition method]`, `comment[sdrf version]` and `comment[sdrf template]`. The validator reports six messages covering these four missing columns. Ontology validation was disabled. Import success does not imply submission readiness.

The [ProteoBench HYE FASTA](https://proteobench.cubimed.rub.de/fasta/ProteoBenchFASTA_MixedSpecies_HYE.zip) contains 31,889 entries with no duplicate identifiers: 20,386 human, 6,721 yeast, 4,400 E. coli, 381 contaminants and one iRT fusion entry. The old FASTA URL in the versioned documentation returned 404, so the current authors' link was used.

## Verified workflow

| Check | Result |
| --- | --- |
| PRIDE download through REST | 3,459,277,876 bytes, successfully resumed after an app restart |
| Imported SDRF | Three rows, three linked samples |
| Source spectrum catalog | 135,859 spectra, including 109,507 MS2 scans |
| Conversion dispatch | Queued through MCP and executed by the Spectarr converter worker |
| Corrected conversion | 596,704,686-byte self-indexed mzML gzip |
| Converted catalog | All 135,859 spectra preserved, all 109,507 MS2 scans centroided |
| Spectrum retrieval | RAW and derived scan 5280 both return 21 peaks, agreeing within relative tolerance 0.000001 and absolute tolerance 0.00001 |
| Precursor metadata | Scan 5280 has m/z 352.515158570517, charge 3 and HCD energy 27 |
| Artifact integrity | RAW and final mzML sizes and SHA-256 hashes matched Spectarr's records |
| MCP connection | Initialization, tool discovery, reads, conversion dispatch, retry, metadata extraction, SDRF validation and run annotation exercised |

The final profile is **HYE Sage centroid mzML**. Its artifact ID is `d21fbcda-326d-44c6-8e57-467cc67788ea`, and its library filename ends in `__d21fbcda.mzML.gz`. The earlier **HYE Sage mzML** profile is disabled. Its retained derivative was an intermediate diagnostic output with profile MS2 scans and must not be used for Sage.

## Sage results

The installed Sage executable reports `0.15.0-beta.2`. The final search used four Rayon threads and completed in 89.3 seconds measured externally. Sage reported 83 seconds internally, including 56.9 seconds for spectrum searching.

| Metric | Count |
| --- | ---: |
| Output PSM rows before filtering | 105,812 |
| Target PSMs at 1% spectrum FDR | 59,281 |
| Target peptides at 1% peptide FDR | 40,292 |
| Target proteins at 1% protein FDR, supported by proteotypic peptides | 5,686 |
| Target protein groups at 1% group FDR, supported by proteotypic peptides | 5,774 |

The accepted PSMs include 40,670 assigned exclusively to human proteins, 14,902 to yeast and 801 to E. coli. Another 2,279 include contaminant assignments, 570 are shared between species and 59 have other assignments. These counts are identification results, not estimates of the mixture's abundance ratios.

Search settings follow the benchmark recommendations: Trypsin/P, two missed cleavages, minimum peptide length seven, fixed carbamidomethylation on C, variable oxidation on M and protein N-terminal acetylation, 10 ppm precursor tolerance and 0.02 Da fragment tolerance. Additional settings are in the saved JSON, including maximum peptide length 50, at most two variable modifications, generated reversed decoys, isotope errors from -1 to 3, one reported PSM per spectrum, and disabled chimeric searching and LFQ.

## Issues found and corrected

- Spectarr ignored `.mzML.gz` files emitted by named presets. Output discovery and validation now accept compressed files and create the embedded index without adding `.gz.gz`. Regression tests cover successful indexing and rejection of truncated gzip.
- OpenMassSpec 1.5.4 returns precursor metadata inside a nested object. The adapter now preserves precursor m/z, charge, isolation width, energy and activation, while retaining compatibility with flat attributes.
- The dashboard copied a hardcoded MCP endpoint. `SPECTARR_MCP_PUBLIC_URL` now supports custom published ports and proxy paths, with a clearly labeled default when unset.
- The scientific metadata panel now describes its values as the latest observations from the run's artifacts, since a derived file's extraction can supersede the source's extraction.
- The `msconvert-cli` Sage preset only centroided MS1. It now uses `peakPicking vendor msLevel=1-`, and its real-file integration test checks centroid representation in every emitted spectrum.

One compatibility limitation remains outside these fixes: the installed Sage reader consumes only the first gzip member. Directly searching the indexed gzip produced zero PSMs with exit code 0. A losslessly decompressed mzML copy solved that read problem. The initial plain mzML attempt then exposed the MS2 centroiding defect, which was corrected before the successful search. Both failed attempts and their logs are retained under `results` for comparison. A successful process exit alone is not the acceptance criterion.

## Reproduction and saved evidence

All large files and execution records are in `/home/ty/Repos/spectarr/data/hye-validation`:

- `start-app.sh` starts the separate local validation instance.
- `run_pipeline.py` resumes the recorded app workflow, verifies artifact hashes, expands gzip for Sage, and rejects empty or zero-identification search results.
- `verify_spectra.py` checks both catalogs, centroid coverage, precursor metadata and the example peak arrays.
- `sage-input.json`, `sage-command.json`, `sage-preset.txt`, `runtime-versions.json` and `sage-binary.json` preserve the actual configuration and executable identity. The runtime used msconvert-cli 1.2.0 with the recorded local Sage preset correction.
- `results/results.sage.tsv` contains unfiltered search results. Use `label == 1` and `spectrum_q <= 0.01` for the accepted PSM set.
- `results/results.json`, `sage.log`, `search-summary.json`, `spectrum-validation.json` and `final-manifest.json` contain results and provenance.
- `mcp-search-annotation.json` records the summary added to the run through MCP.

The final Sage command is recorded as an argument array. An equivalent shell invocation from the repository root is:

```sh
RAYON_NUM_THREADS=4 data/hye-validation/bin/sage data/hye-validation/sage-input.json --batch-size 1 --disable-telemetry-i-dont-want-to-improve-sage
```

This reuses the saved expanded mzML. To expand an indexed gzip for another search, use a gzip reader that processes every member, such as Python's `gzip.open` or `gzip -dc`.

| File | SHA-256 |
| --- | --- |
| RAW source | `bc7c31ee9188941ab5e4d8069b2f73a0e7a57734c23447eb4bb3b870c773d630` |
| Final indexed mzML gzip | `9b028d98e62000580758361fb26ca1b991fd4fa4121b218065d8bb58d359db9b` |
| Expanded mzML searched by Sage | `9193e0b03234a20cd8575bd7af82744259f5d175377a15fef9b8092d884b91f5` |
| Reference FASTA | `d9ac434d88492c10c8e9a587ee7dbc9480fa0995fa07a6ba35a7da8abf39aa25` |
| Final PSM TSV | `5ba5b53f5d4d7ebbe70b7bd873dea9e56734b8ca854a0e2242ffa169f6101862` |

Verification also passed 57 converter and extractor unit tests, 21 frontend tests, two focused API checks, five msconvert-cli CLI checks, one real-file msconvert-cli Sage preset integration test, the frontend production build and Git whitespace checks. Live browser checks confirmed the imported samples, scientific metadata, download progress and corrected MCP endpoint. This is one acquisition's integration test, not a full LFQ benchmark or a guarantee for every supported instrument format.
