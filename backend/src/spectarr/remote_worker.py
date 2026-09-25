"""Durable PRIDE downloads with process-owned slot and item locks."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from datetime import timedelta
from uuid import UUID, uuid5

import httpx
from sqlalchemy import and_, or_, select

from . import pride, remote_sdrf
from .api import create_artifact_record, infer_format
from .config import get_settings
from .database import SessionLocal
from .locking import file_lock, maintenance_lock
from .models import Artifact, ArtifactRole, RemoteImport, Run, RunSample, Sample, utcnow
from .pipeline import schedule_source_pipeline
from .remote_capacity import CapacityBusy, item_lock, reserve, release
from .remote_settings import download_settings
from .storage import LocalArtifactStorage

ACTIVE = {"queued", "downloading", "verifying", "registering"}


class Cancelled(Exception):
    pass


class Stopped(Exception):
    pass


def storage_for_settings():
    settings = get_settings()
    return LocalArtifactStorage(settings.storage_root, settings.library_root, settings.library_link_mode,
                                settings.library_project_template, settings.library_filename_template)


def checkpoint(import_id, stop, **changes):
    if stop.is_set():
        raise Stopped()
    with SessionLocal() as session:
        item = session.get(RemoteImport, import_id)
        if item is None or item.cancel_requested or item.state == "cancelled":
            raise Cancelled()
        for key, value in changes.items():
            setattr(item, key, value)
        session.commit()


def transfer(item, path, stop):
    url = pride.download_url(item.source["url"])
    pride.check_public_host("ftp.pride.ebi.ac.uk")
    offset = path.stat().st_size if path.exists() else 0
    validator = item.validator
    if offset > item.byte_size or not validator:
        offset = 0
    settings = get_settings()
    # Account for the remaining payload, ingestion copy, and possible library copy.
    copies = 2 if settings.library_link_mode == "hardlink" else 3
    storage = storage_for_settings()
    reserve(storage, item.id, item.byte_size, offset, copies)
    if offset == item.byte_size:
        return
    headers = {"Accept-Encoding": "identity"}
    if offset:
        headers.update({"Range": f"bytes={offset}-", "If-Range": validator})
    with pride.client() as http, http.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        if response.status_code not in {200, 206} or response.headers.get("content-encoding", "identity") != "identity":
            raise ValueError("Unexpected repository download response")
        etag = response.headers.get("etag", "")
        remote_validator = etag if etag and not etag.startswith("W/") else response.headers.get("last-modified")
        if response.status_code == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("content-range", ""))
            if (not match or int(match[1]) != offset or int(match[2]) != item.byte_size - 1
                    or int(match[3]) != item.byte_size or offset and remote_validator != validator):
                with maintenance_lock(settings.storage_root, exclusive=False, blocking=True):
                    path.unlink(missing_ok=True)
                raise ValueError("Repository content or byte range changed. Retry to restart the download")
        else:
            offset = 0
        length = response.headers.get("content-length")
        if length is not None and int(length) != item.byte_size - offset:
            # Archive metadata may report an uncompressed or outdated size.
            # A complete HTTPS response establishes the transfer size. Preserve
            # the repository size in source metadata and still verify its checksum.
            if response.status_code != 200 or not 0 < int(length) <= settings.max_upload_bytes:
                raise ValueError("Repository response has an invalid or oversized file size")
            item.byte_size = int(length)
            reserve(storage, item.id, item.byte_size, 0, copies)
            checkpoint(item.id, stop, byte_size=item.byte_size)
        with maintenance_lock(settings.storage_root, exclusive=False, blocking=True):
            checkpoint(item.id, stop, validator=remote_validator, bytes_received=offset)
            with path.open("r+b" if path.exists() else "w+b") as output:
                output.truncate(offset)
        last_update = time.monotonic()
        for chunk in response.iter_raw(1024 * 1024):
            with maintenance_lock(settings.storage_root, exclusive=False, blocking=True):
                checkpoint(item.id, stop)
                if offset + len(chunk) > item.byte_size:
                    raise ValueError("Repository sent more bytes than the selected file size")
                with path.open("r+b") as output:
                    output.seek(offset)
                    output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                offset += len(chunk)
                if time.monotonic() - last_update >= 1:
                    checkpoint(item.id, stop, bytes_received=offset, reserved_bytes=max(0, copies * item.byte_size - offset))
                    last_update = time.monotonic()
        checkpoint(item.id, stop, bytes_received=offset, reserved_bytes=max(0, copies * item.byte_size - offset))
        if offset != item.byte_size:
            raise httpx.ReadError("Download ended before the expected file size")


def verify(item, path, stop):
    expected = item.source.get("checksum")
    digest = hashlib.new(expected["algorithm"]) if expected else None
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            checkpoint(item.id, stop)
            size += len(chunk)
            if digest:
                digest.update(chunk)
    if size != item.byte_size or digest and digest.hexdigest() != expected["value"]:
        path.unlink(missing_ok=True)
        raise ValueError("Downloaded file failed repository size or checksum verification")


def register(item, path, storage, stop):
    run_id = str(uuid5(UUID(item.id), "run"))
    artifact_id = str(uuid5(UUID(item.id), "artifact"))
    with file_lock(storage.internal / "remote-registration.lock", exclusive=True, blocking=True):
        with maintenance_lock(storage.root, exclusive=False, blocking=True):
            with SessionLocal() as probe:
                registered = probe.get(Artifact, artifact_id) is not None
            checkpoint(item.id, stop)
            stored = None if registered else storage.ingest_path(path)
        with maintenance_lock(storage.root, exclusive=True, blocking=True), SessionLocal() as session:
            checkpoint(item.id, stop)
            # Reconcile a crash after artifact commit without downloading again.
            existing = session.get(Artifact, artifact_id)
            if existing:
                schedule_source_pipeline(session, existing)
            else:
                stored = stored or storage.ingest_path(path)
                checkpoint(item.id, stop)
                run = session.get(Run, run_id)
                if run is None:
                    sample = session.scalar(select(Sample).where(Sample.experiment_id == item.experiment_id, Sample.name == item.sample_name))
                    if sample is None and not item.source.get("sdrf"):
                        sample = Sample(experiment_id=item.experiment_id, name=item.sample_name)
                        session.add(sample)
                        session.flush()
                    kind = infer_format(item.filename)
                    source_class = "open" if kind in {"mzML", "mzXML"} else "vendor" if kind == "RAW" else "spectrum_list"
                    run = Run(id=run_id, experiment_id=item.experiment_id, sample_id=sample.id if sample and not item.source.get("sdrf") else None,
                              name=item.run_name, source_class=source_class,
                              metadata_json={"repository": "PRIDE", "accession": item.accession})
                    session.add(run)
                    session.flush()
                    if not item.source.get("sdrf"):
                        session.add(RunSample(run_id=run_id, sample_id=sample.id))
                create_artifact_record(
                    session, storage=storage, run_id=run_id, stored=stored, filename=item.filename,
                    role=ArtifactRole.SOURCE, artifact_format=infer_format(item.filename),
                    parent_artifact_id=None, recipe_id=None, artifact_id=artifact_id,
                    metadata_json={"remote_import_id": item.id, "repository": "PRIDE", "accession": item.accession,
                                   "repository_file_id": item.file_id, "source_url": item.source["url"],
                                   "repository_checksum": item.source.get("checksum"),
                                   "repository_reported_bytes": item.source["byte_size"], "downloaded_bytes": item.byte_size,
                                   "dataset": item.source["dataset"], "retrieved_at": item.source["retrieved_at"]},
                )
            artifact = session.get(Artifact, artifact_id)
            remote_sdrf.apply(session, artifact, item.source.get("sdrf"), storage)
            current = session.get(RemoteImport, item.id)
            current.run_id = run_id
            current.artifact_id = artifact_id
            current.state = "succeeded"
            current.bytes_received = item.byte_size
            current.error = None
            session.commit()
            path.unlink(missing_ok=True)


def process_item(item, root, storage, stop):
    path = root / f"{item.id}.part"
    try:
        checkpoint(item.id, stop)
        with SessionLocal() as session:
            registered = session.get(Artifact, str(uuid5(UUID(item.id), "artifact"))) is not None
        if not registered:
            transfer(item, path, stop)
            checkpoint(item.id, stop, state="verifying")
            with maintenance_lock(storage.root, exclusive=False, blocking=True):
                verify(item, path, stop)
        if not registered and "retrieved_at" not in item.source:
            item.source = {**item.source, "retrieved_at": utcnow().isoformat()}
            checkpoint(item.id, stop, source=item.source)
        checkpoint(item.id, stop, state="registering")
        register(item, path, storage, stop)
    except Stopped:
        return
    except Exception as error:
        transient = isinstance(error, (httpx.TransportError, BlockingIOError)) or (
            isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {408, 429, 500, 502, 503, 504}
        )
        with SessionLocal() as session:
            current = session.get(RemoteImport, item.id)
            if current:
                current.state = "cancelled" if isinstance(error, Cancelled) or current.cancel_requested else (
                    "queued" if isinstance(error, CapacityBusy) or transient and current.attempts < 3 else "failed"
                )
                if isinstance(error, CapacityBusy):
                    current.attempts = max(0, current.attempts - 1)
                current.error = None if current.state == "cancelled" else str(error)[:2000]
                current.retry_at = utcnow() + timedelta(seconds=5 if isinstance(error, CapacityBusy) else 30 * current.attempts) if current.state == "queued" else None
                session.commit()
        if not isinstance(error, (Cancelled, CapacityBusy)):
            logging.getLogger(__name__).warning("Remote import %s interrupted: %s", item.id, error)
    finally:
        release(storage, item.id)


def cleanup_staging(storage, root):
    with SessionLocal() as session:
        keep = set(session.scalars(select(RemoteImport.id).where(or_(
            RemoteImport.state.in_(ACTIVE),
            and_(RemoteImport.state == "failed", RemoteImport.updated_at > utcnow() - timedelta(days=7)),
        ))))
    for path in root.glob("*.part"):
        if path.stem in keep:
            continue
        try:
            with item_lock(storage, path.stem), maintenance_lock(storage.root, exclusive=False, blocking=True):
                # A retry may have arrived since the first query.
                with SessionLocal() as session:
                    item = session.get(RemoteImport, path.stem)
                    if item and item.state in ACTIVE:
                        continue
                path.unlink(missing_ok=True)
        except BlockingIOError:
            pass


def tick(stop: threading.Event | None = None, slot: int = 0):
    stop = stop or threading.Event()
    settings = get_settings()
    if settings.restore_mode or not settings.remote_imports_enabled:
        return
    concurrency = download_settings()["concurrency"]
    if not 0 <= slot < concurrency:
        return
    storage = storage_for_settings()
    try:
        with file_lock(storage.internal / f"remote-slot-{slot}.lock", exclusive=True):
            root = storage.staging / "remote-imports"
            root.mkdir(exist_ok=True)
            if slot == 0:
                cleanup_staging(storage, root)
            with SessionLocal() as session:
                candidates = list(session.scalars(select(RemoteImport.id).where(
                    RemoteImport.state.in_(ACTIVE),
                    or_(RemoteImport.retry_at.is_(None), RemoteImport.retry_at <= utcnow()),
                ).order_by(RemoteImport.created_at, RemoteImport.id).limit(concurrency + 1)))
            for import_id in candidates:
                try:
                    with item_lock(storage, import_id):
                        with SessionLocal() as session:
                            item = session.get(RemoteImport, import_id)
                            if not item or item.state not in ACTIVE:
                                continue
                            item.state = "downloading"
                            item.attempts += 1
                            session.commit()
                        process_item(item, root, storage, stop)
                        return
                except BlockingIOError:
                    continue
    except BlockingIOError:
        pass
