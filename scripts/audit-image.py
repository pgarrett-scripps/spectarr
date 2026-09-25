#!/usr/bin/env python3
"""Audit an exact local image and retain full reports, including unfixed findings."""

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path


SCANNER = "aquasec/trivy@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    image = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True).strip()
    (output / "IMAGE").write_text(image + "\n")
    code = "\n".join([
        "import importlib.metadata as metadata",
        "import json",
        "print(json.dumps(sorted([{'name': d.metadata['Name'], 'version': d.version} for d in metadata.distributions()], key=lambda d: d['name'])))",
    ])
    packages = json.loads(subprocess.check_output(["docker", "run", "--rm", "--entrypoint", "python", image, "-c", code], text=True))
    (output / "packages.json").write_text(json.dumps(packages, indent=2))
    excluded = [p for p in packages if p["name"].startswith("spectarr") or p["name"] == "msconvert-cli"]
    (output / "local-source-packages.json").write_text(json.dumps(excluded, indent=2))
    requirements = output / "requirements.txt"
    requirements.write_text("".join(f"{p['name']}=={p['version']}\n" for p in packages if p not in excluded))
    audit = subprocess.run([
        "uv", "tool", "run", "pip-audit==2.10.1", "-r", str(requirements), "--no-deps", "--disable-pip",
        "--format=json", "--output", str(output / "python.json"),
    ])
    cache = output.parent / "trivy-cache"
    cache.mkdir(exist_ok=True)
    with (output / "image.json").open("w") as report:
        subprocess.run([
            "docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock:ro",
            "-v", f"{cache}:/root/.cache/", SCANNER, "image", "--scanners", "vuln", "--format", "json", image,
        ], stdout=report, check=True)
    data = json.loads((output / "image.json").read_text())
    findings = [v for r in data.get("Results", []) for v in r.get("Vulnerabilities", [])]
    fixable = [v for v in findings if v.get("FixedVersion")]
    summary = {
        "image": image,
        "findings_by_severity": dict(Counter(v["Severity"] for v in findings)),
        "fixable_by_severity": dict(Counter(v["Severity"] for v in fixable)),
        "python_audit_exit_code": audit.returncode,
        "local_source_packages_excluded_from_pypi_audit": excluded,
        "note": "An empty fixable count does not mean the image has no vulnerabilities. Review the complete report.",
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    if audit.returncode or any(v["Severity"] in {"HIGH", "CRITICAL"} for v in fixable):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
