# Real-file acceptance testing

The single-container SQLite build was exercised on 2026-08-27 with authorized vendor fixtures and the pinned ProteoWizard image. Results are stored in the `Release acceptance 79a61546` project in the local dashboard. The reusable runner is `scripts/vendor-acceptance.py`.

## Verified inputs

| Input | Size | Result |
| --- | ---: | --- |
| Thermo `Angiotensin_325-CID.raw` | 419 KB | OpenMassSpec extracted 10 spectra directly. MSConvert generated mzML, mzXML, MGF, and MS2. Authenticated downloads matched every recorded SHA-256 value. |
| Vendor `A_1_neg_2100u15c_BEH130_18Jul2025.raw` | 35.5 MB | OpenMassSpec reported the expected unexpected-end-of-file warning. The automatic MSConvert fallback generated mzML, then extraction reported 5,445 spectra. |
| Bruker `example_dda.d` | 62.1 MB | Spectarr imported the directory as one immutable artifact with SHA-256 `408ac611713b18688e790e46266956d68aa1872f621a1d56fce9bdff48baf9fd`. Conversion and extraction reported 476,410 spectra. |

The Thermo derivative checksums were:

| Format | Bytes | SHA-256 |
| --- | ---: | --- |
| mzML | 227,892 | `3f59462c45c76f9c96a166f9819422af97501eba900f0332c77951eb84a5e75e` |
| mzXML | 329,563 | `ccd125798bc2ebaee059973596edbfe1316c86ece001809bb8c2e01a25a48525` |
| MGF | 45,103 | `30e2d8cd702892fccc512e6d2c62d894cb9c2da93e1dddb99f05702e136dfef2` |
| MS2 | 378,243 | `a09d97c63ce32c6b2008a91388419df786080eda730b38966aeaced98642d91f` |

## Lifecycle checks

A forced large-RAW conversion reached the running state, was cancelled through its processing batch, was retried, and succeeded. One MGF derivative was then reclaimed. Regeneration produced the same 45,103-byte file and the same SHA-256 value as the original.

The first acceptance attempt found an invalid built-in peak-picking range. The compiler emitted `1,2`, which ProteoWizard rejects. The profile compiler now emits the valid `1-2` range, rejects noncontiguous ranges, and records converter stderr on failure. The complete matrix passed after rebuilding with that correction.

## Automated regression suite

The current suite passes 67 backend tests at 85.6 percent coverage, 52 dashboard tests, and 126 service tests. TypeScript checks, ESLint, the production dashboard build, Compose validation, container health, browser interaction, authenticated conversion, direct spectrum reads, MCP initialization, webhook delivery, concurrent SQLite ingestion, restart recovery, online backup, and independent restore boot are release gates.

Run the portable release rehearsal with:

```bash
scripts/release-rehearsal.sh
```

Run the vendor matrix after placing authorized fixtures below an allowed import root:

```bash
SPECTARR_SMOKE_URL=http://127.0.0.1:3280/api/v1 \
python3 scripts/vendor-acceptance.py \
  --thermo /imports/acceptance/thermo.raw \
  --large-raw /imports/acceptance/large-vendor.raw \
  --bruker /imports/acceptance/example.d \
  --output vendor-acceptance.json
```

The runner does not download proprietary data or accept vendor licenses. Run it only on an authorized x86-64 host.
