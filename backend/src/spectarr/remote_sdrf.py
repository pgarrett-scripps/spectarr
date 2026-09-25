"""Preview repository SDRF and append rows only for the acquired files."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy import select

from . import pride
from .library import LibraryMaterializer
from .models import RunSample, Sample, SdrfDocument, SdrfRow
from .sdrf import BASE_TEMPLATE, RESERVED_VALUES, parse_sdrf, serialize_sdrf

MAX_SDRF_BYTES = 5 * 1024 * 1024


def preview(dataset, file_id):
    source = next((file for file in dataset["files"] if file["id"] == file_id and file.get("is_sdrf")), None)
    if not source:
        raise HTTPException(422, "Select an available SDRF file from this PRIDE dataset")
    url = pride.download_url(source["url"])
    pride.check_public_host("ftp.pride.ebi.ac.uk")
    with pride.client() as http, http.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise HTTPException(422, "Unexpected SDRF download response")
        content = bytearray()
        for chunk in response.iter_bytes(65536):
            content.extend(chunk)
            if len(content) > MAX_SDRF_BYTES:
                raise HTTPException(413, "Repository SDRF files are limited to 5 MiB")
    expected = source.get("checksum")
    if expected and hashlib.new(expected["algorithm"], content).hexdigest() != expected["value"]:
        raise HTTPException(422, "SDRF repository checksum mismatch")
    columns, rows = parse_sdrf(bytes(content))
    normalized = [column.casefold().strip() for column in columns]
    for name in ("source name", "comment[data file]"):
        if normalized.count(name) != 1:
            raise HTTPException(422, f"SDRF requires exactly one {name} column")
    file_column = normalized.index("comment[data file]")
    sample_column = normalized.index("source name")
    by_name = defaultdict(list)
    for file in dataset["files"]:
        if file["supported"]:
            by_name[file["filename"]].append(file["id"])
    mappings = defaultdict(list)
    unmatched = 0
    invalid = 0
    for index, row in enumerate(rows):
        filename = PurePosixPath(row[file_column].replace("\\", "/")).name
        matches = by_name[filename]
        if len(matches) != 1:
            unmatched += 1
            continue
        if not row[sample_column] or row[sample_column].casefold() in RESERVED_VALUES or len(row[sample_column]) > 255:
            invalid += 1
            continue
        mappings[matches[0]].append(index)
    warnings = []
    if unmatched:
        warnings.append(f"{unmatched} rows have no unique supported acquisition filename match")
    if invalid:
        warnings.append(f"{invalid} rows have missing or unsupported sample names")
    if "assay name" not in normalized:
        warnings.append("No assay name column is present. Mapping uses acquisition filenames")
    return {"file_id": file_id, "filename": source["filename"], "url": url,
            "sha256": hashlib.sha256(content).hexdigest(), "columns": columns, "rows": rows,
            "mappings": dict(mappings), "warnings": warnings}


def subset(document, file_id):
    indices = document["mappings"].get(file_id, [])
    if not indices:
        return None
    return {"file_id": document["file_id"], "filename": document["filename"], "url": document["url"],
            "sha256": document["sha256"], "columns": document["columns"],
            "rows": [document["rows"][index] for index in indices], "source_rows": [index + 2 for index in indices]}


def column_keys(columns):
    counts = Counter()
    keys = []
    for column in columns:
        name = column.casefold().strip()
        keys.append((name, counts[name]))
        counts[name] += 1
    return keys


def apply(session, artifact, snapshot, storage):
    if not snapshot:
        return
    if artifact.metadata_json.get("sdrf_import"):
        materializer = LibraryMaterializer(storage)
        materializer.write_project_manifest(artifact.run.experiment.project)
        materializer.materialize_artifact(artifact)
        return
    run = artifact.run
    project = run.experiment.project
    document = project.sdrf_document
    if document is None:
        document = SdrfDocument(project=project, columns=[], templates=[BASE_TEMPLATE],
                                source_filename=snapshot["filename"], revision=0)
        session.add(document)
        session.flush()
    old_keys = column_keys(document.columns)
    incoming_keys = column_keys(snapshot["columns"])
    merged_columns = list(document.columns)
    merged_keys = list(old_keys)
    for key, column in zip(incoming_keys, snapshot["columns"], strict=True):
        if key not in merged_keys:
            merged_keys.append(key)
            merged_columns.append(column)
    positions = {key: index for index, key in enumerate(merged_keys)}
    if len(merged_columns) != len(document.columns):
        for row in document.rows:
            row.values = row.values + ["not available"] * (len(merged_columns) - len(row.values))
    document.columns = merged_columns
    normalized = [column.casefold().strip() for column in snapshot["columns"]]
    source_column = normalized.index("source name")
    label_column = normalized.index("comment[label]") if "comment[label]" in normalized else None
    run.sample_links.clear()
    run.sample = None
    session.flush()
    links = set()
    next_position = max((row.position for row in document.rows), default=-1) + 1
    for offset, values in enumerate(snapshot["rows"]):
        name = values[source_column]
        sample = session.scalar(select(Sample).where(Sample.experiment_id == run.experiment_id, Sample.name == name))
        characteristics = {column: values[i] for i, column in enumerate(normalized) if column.startswith("characteristics[")}
        if sample is None:
            sample = Sample(experiment_id=run.experiment_id, name=name, metadata_json=characteristics)
            session.add(sample)
            session.flush()
        else:
            # Keep existing sample annotations when the repository disagrees.
            sample.metadata_json = {**characteristics, **sample.metadata_json}
        label = (values[label_column] if label_column is not None else "") or "label free sample"
        key = (sample.id, label)
        if key not in links:
            session.add(RunSample(run_id=run.id, sample_id=sample.id, label=label, position=len(links),
                                  metadata_json={column: values[i] for i, column in enumerate(normalized) if column.startswith("factor value[")}))
            links.add(key)
        aligned = ["not available"] * len(merged_columns)
        for key, value in zip(incoming_keys, values, strict=True):
            aligned[positions[key]] = value
        document.rows.append(SdrfRow(position=next_position + offset, values=aligned, run_id=run.id,
                                    artifact_id=artifact.id, sample_id=sample.id))
    if len({sample_id for sample_id, _ in links}) == 1:
        run.sample_id = next(iter(links))[0]
    run.metadata_json = {**run.metadata_json, "sdrf": {
        column: snapshot["rows"][0][i] for i, column in enumerate(normalized) if column.startswith("comment[")
    }}
    artifact.metadata_json = {**artifact.metadata_json, "sdrf_import": {
        "filename": snapshot["filename"], "sha256": snapshot["sha256"], "url": snapshot["url"],
        "source_rows": snapshot["source_rows"], "imported_rows": len(snapshot["rows"]),
    }}
    document.revision += 1
    document.status = "draft"
    document.validation_engine = None
    document.validation_report = {}
    document.content_sha256 = hashlib.sha256(serialize_sdrf(document.columns, [row.values for row in document.rows])).hexdigest()
    session.commit()
    session.expire(run, ["sample_links", "sample", "samples"])
    materializer = LibraryMaterializer(storage)
    materializer.write_project_manifest(project)
    materializer.materialize_artifact(artifact)
