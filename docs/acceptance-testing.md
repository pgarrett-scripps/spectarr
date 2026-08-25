# Real-file acceptance testing

The local stack was exercised on 2026-08-25 with real vendor and open-format files. Acceptance data is stored in the `Format Acceptance 2026-08-25` project so the results can be reviewed in the dashboard.

## Verified inputs

| Input | Size | Result |
| --- | ---: | --- |
| Thermo `Angiotensin_325-CID.raw` | 419 KB | OpenMassSpec extracted 10 MS2 spectra directly. MSConvert generated mzML, mzXML, MGF, and MS2 derivatives. Every derivative passed validation and metadata extraction. |
| Vendor `A_1_neg_2100u15c_BEH130_18Jul2025.raw` | 35.5 MB | MSConvert generated an 89.4 MB mzML. Extraction reported 5,445 spectra, 2,000 MS2 spectra, negative polarity, and a 23.99 minute acquisition. |
| `example.mzML` | 25 KB | Extraction reported 4 spectra, including 3 MS1 and 1 MS2 spectrum. |
| `example.mzML.gz` | 3.3 KB | Content-addressed gzip detection produced the same summary as the uncompressed mzML. |
| Bruker ion-mobility mzML | 6.9 MB | Extraction reported 10 MS2 spectra and detected ion mobility. |
| Casanovo MGF | 2.7 KB | Extraction reported 2 MS2 spectra and a 0.894 second duration. |

Generated artifacts were downloaded through the authenticated API and checked against their recorded SHA-256 values. The 89.4 MB mzML checksum was `07ba553b84d86bb5282fe8d9a6852d024c15e0b110e3ce8438dd9dbbb038272e`.

## Compatibility behavior

OpenMassSpec 1.5.4 successfully read the small Thermo RAW fixture. It rejected the larger vendor RAW fixture with an unexpected end-of-file error. Spectarr preserved that provider failure as a warning, then completed conversion and QC through the pinned ProteoWizard image. This fallback is intentional because vendor-reader coverage varies by acquisition and release.

The ProteoWizard image is pinned to the published `3.0.26121-ed8dc8a` tag. The previously configured `3.0.23310` tag did not exist and was replaced during acceptance testing.

## Regression suite

The current suite passes 45 backend tests, 21 dashboard tests, and 102 service tests. Backend coverage is enforced at a minimum of 85 percent. One MCP loopback socket test is skipped when the sandbox prevents opening a local listener. TypeScript checks, ESLint, the production dashboard build, Compose validation, container health checks, and browser interaction checks also pass.
