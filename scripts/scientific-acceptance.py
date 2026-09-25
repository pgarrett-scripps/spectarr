#!/usr/bin/env python3
"""Compare catalog observations with independent XML and vendor database counts."""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

import smoke_test as api


def xml_counts(path: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        for _event, element in ElementTree.iterparse(stream, events=("end",)):
            if element.tag.rsplit("}", 1)[-1] != "spectrum":
                continue
            level = next((node.attrib["value"] for node in element.iter()
                          if node.attrib.get("accession") == "MS:1000511"), "unknown")
            counts[level] += 1
            element.clear()
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor-results", required=True, type=Path)
    parser.add_argument("--library-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    vendor = json.loads(args.vendor_results.read_text())
    token = api.authenticate()
    evidence = {}
    for name, fixture in vendor["fixtures"].items():
        run_id = fixture["run_id"]
        run = api.json_call("GET", f"/runs/{run_id}", token=token)
        artifacts = api.json_call("GET", f"/runs/{run_id}/artifacts", token=token)
        output = next(row for row in artifacts if row["role"] == "derived"
                      and row["format"].lower() == "mzml" and row["state"] == "ready")
        access = api.json_call("GET", f"/artifacts/{output['id']}/access", token=token)
        path = args.library_root / access["library_relative_path"]
        counts = xml_counts(path)
        extraction = api.wait_for("derived metadata", lambda output_id=output["id"]: api.json_call(
            "GET", f"/artifacts/{output_id}/extraction-results/latest", token=token),
            lambda row: bool(row), 600)
        summary = extraction["payload"]["qc_summary"]
        assert sum(counts.values()) == summary["spectrum_count"], (counts, summary)
        assert counts == {str(key): value for key, value in summary["spectra_by_ms_level"].items()}
        if name == "bruker":
            source = next(row for row in artifacts if row["id"] == fixture["source_artifact_id"])
            source_path = args.library_root / source["library_path"] / "analysis.tdf"
            with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as database:
                frames = database.execute("SELECT count(*) FROM Frames").fetchone()[0]
                ms1 = database.execute("SELECT count(*) FROM Frames WHERE MsMsType = 0").fetchone()[0]
                pasef = database.execute("SELECT count(*) FROM PasefFrameMsMsInfo").fetchone()[0]
            assert run["spectraCount"] == ms1 + pasef
            assert run["latest_extraction"]["payload"]["qc_summary"]["spectra_by_ms_level"] == {"1": ms1, "2": pasef}
            assert run["summary_basis"]["reason"] == "source"
            evidence[name] = {"vendor_frames": frames, "source_ms1_frames": ms1,
                              "source_pasef_entries": pasef, "source_records": ms1 + pasef,
                              "converted_spectra": sum(counts.values()),
                              "note": "Source records match MS1 frames plus PASEF entries. Converted spectra are separate observations."}
        else:
            assert run["spectraCount"] == sum(counts.values())
            evidence[name] = {"run_spectra": run["spectraCount"], "xml_spectra": sum(counts.values())}
        evidence[name]["xml_ms_levels"] = counts
        evidence[name]["summary_basis"] = run["summary_basis"]
    args.output.write_text(json.dumps({"status": "ok", "evidence": evidence}, indent=2) + "\n")
    print(json.dumps({"status": "ok", "independently_checked_acquisitions": len(evidence)}))


if __name__ == "__main__":
    main()
