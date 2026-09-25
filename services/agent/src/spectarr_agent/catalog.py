"""Explicit external inventory operations with a local folder allowlist."""
import json
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from .api import ApiError, SpectarrAgentApi
from .discovery import AcquisitionChanged, AcquisitionScanner, Candidate, Snapshot
from .readonly import directory_handle, root_identity
from .state import QueueItem
from .uploader import ResumableUploader, SourceUnavailable


class ImportApi:
    def __init__(self, api, task_id):
        self.api = api
        self.task_id = task_id

    def __getattr__(self, name):
        return getattr(self.api, name)

    def create_upload(self, token, key, **kwargs):
        kwargs["metadata"] = {"external_task_id": self.task_id}
        return self.api.create_upload(token, "external-import:" + self.task_id, **kwargs)


class ImportState:
    def __init__(self, state):
        self.state = state

    def set_upload_id(self, task_id, upload_id):
        self.state.set_metadata("external-upload:" + task_id, upload_id)


class CatalogAgent:
    def __init__(self, config, state, api: SpectarrAgentApi, token):
        self.config = config
        self.state = state
        self.api = api
        self.token = token
        self.scanner = AcquisitionScanner(config)

    def call(self, method, path, body=None):
        return self.api._request(method, "/api/v1/external-agent/" + path, token=self.token, json_body=body)[0]

    def tick(self):
        for task in self.call("GET", "tasks")["items"]:
            try:
                if task["kind"] == "import":
                    saved = self.state.metadata("external-upload:" + task["id"])
                    if saved:
                        uploaded = self.api.get_upload(self.token, saved)
                        if uploaded.get("state") == "completed":
                            self.finish(task, status="complete", identity=task["root"]["identity"], artifact_id=uploaded["artifact_id"])
                            continue
                root, identity = self.root(task)
                if task["kind"] == "scan":
                    self.scan(task, root, identity)
                else:
                    self.verify_or_import(task, root, identity)
            except (OSError, ValueError, AcquisitionChanged, SourceUnavailable) as error:
                self.finish(task, status="unavailable" if isinstance(error, OSError) else "failed", error=str(error)[:2000])
            except ApiError as error:
                if error.retryable:
                    raise
                if error.status == 409:
                    self.finish(task, status="failed", error=error.detail[:2000])
                else:
                    raise

    def root(self, task):
        root = Path(task["root"]["path"])
        if root not in self.config.watch_paths:
            raise ValueError("Folder is not in this agent's local allowlist")
        if root == Path(root.anchor):
            raise ValueError("Whole filesystem discovery is not allowed")
        for parent in (root, *root.parents):
            if parent.is_symlink():
                raise ValueError("Linked root path")
            if (parent / ".spectarr").exists():
                raise ValueError("Managed storage cannot be an external archive")
        identity = root_identity(root)
        expected = task["root"].get("identity")
        if expected and expected != identity:
            raise ValueError("Root identity changed. Revalidate it in MassSpec")
        return root, identity

    def scan(self, task, root, identity):
        key = "external-scan:" + task["id"]
        saved = self.state.metadata(key)
        if saved:
            report = json.loads(saved)
            if report["identity"] != identity:
                raise ValueError("Root changed during a resumed scan")
        else:
            report = {"identity": identity, "files": [], "errors": [], "visited": 0}
            if task["sequence"]:
                report["errors"].append("Local scan checkpoint was lost. Missing reconciliation is disabled")
            with self.state.transaction():
                self.walk(root, root, report)
            if root_identity(root) != identity:
                raise ValueError("Root changed during scanning")
            self.state.set_metadata(key, json.dumps(report))
        files = report["files"]
        batches = [files[i:i + 200] for i in range(0, len(files), 200)] or [[]]
        start = task["sequence"] if saved else 0
        offset = 0 if saved else task["sequence"]
        for index in range(start, len(batches)):
            self.call("POST", f"tasks/{task['id']}/observations", {"identity": identity, "sequence": index + offset, "files": batches[index]})
        if root_identity(root) != identity:
            raise ValueError("Root changed during reporting")
        self.finish(task, status="partial" if report["errors"] else "complete", identity=identity, error="\n".join(report["errors"])[:2000] or None)
        self.state.set_metadata(key, "")

    def walk(self, root, folder, report):
        if report.get("visited", 0) >= self.config.scan_max_entries:
            report["errors"].append("Scan entry limit reached")
            return
        if len(folder.relative_to(root).parts) > 128:
            report["errors"].append("Folder depth limit reached")
            return
        try:
            with directory_handle(folder) as fd:
                import os
                names = sorted(os.listdir(fd))
                markers = [name.casefold().lstrip("~") for name in names if self.scanner._ignored(name)]
                for name in names:
                    if report.get("visited", 0) >= self.config.scan_max_entries:
                        report["errors"].append("Scan entry limit reached")
                        break
                    report["visited"] = report.get("visited", 0) + 1
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    path = folder / name
                    if stat.S_ISLNK(info.st_mode) or self.scanner._ignored(name):
                        continue
                    if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
                        continue
                    if stat.S_ISDIR(info.st_mode) and info.st_dev != os.fstat(fd).st_dev:
                        report["errors"].append(f"Nested filesystem requires its own root: {name}")
                        continue
                    if self.scanner._is_candidate(path):
                        candidate = self.scanner._candidate(path)
                        if not candidate.path.is_relative_to(root):
                            raise ValueError("Acquisition escaped its root")
                        if candidate.kind == "file":
                            blocker = next((marker for marker in markers if marker.startswith((name.casefold() + ".", path.stem.casefold() + "."))), None)
                            snapshot = Snapshot(f"f:{info.st_size}:{info.st_mtime_ns}", info.st_size, 1, bool(blocker), blocker)
                        else:
                            snapshot = self.scanner.snapshot(candidate)
                        stable = self.state.observe(path, snapshot.signature, time.time(), self.config.stability_seconds, snapshot.reason)
                        readiness = "blocked" if snapshot.blocked or not self.scanner.published(candidate) else "stable_by_observation" if stable else "changing"
                        report["files"].append({"path": path.relative_to(root).as_posix(), "format": candidate.format, "kind": candidate.kind,
                            "signature": snapshot.signature, "byte_size": snapshot.byte_size, "readiness": readiness, "observed_at": datetime.now(timezone.utc).isoformat()})
                    elif stat.S_ISDIR(info.st_mode):
                        self.walk(root, path, report)
        except (OSError, ValueError) as error:
            report["errors"].append(f"{folder.relative_to(root)}: {error}")

    def verify_or_import(self, task, root, identity):
        relative = task["location"]["relative_path"]
        if any(p in {"", ".", ".."} for p in relative.split("/")) or "\\" in relative:
            raise ValueError("Unsafe external location")
        path = root / relative
        with directory_handle(path.parent):
            if path.is_symlink():
                raise ValueError("Linked acquisition")
        candidate = Candidate(path, task["entry"]["kind"], task["entry"]["format"])
        if candidate.format not in {"RAW", "mzML", "mzXML", "MGF", "MS2", "vendor_directory"}:
            raise ValueError("This sidecar-dependent format is inventory only")
        snapshot = self.scanner.snapshot(candidate)
        stable = self.state.observe(path, snapshot.signature, time.time(), self.config.stability_seconds, snapshot.reason)
        if snapshot.blocked or not stable or not self.scanner.published(candidate):
            return
        acquisition = self.scanner.hash_candidate(candidate)
        if root_identity(root) != identity:
            raise ValueError("Root changed during verification")
        readiness = "producer_published" if self.config.completion_policy == "published_marker" else "stable_by_observation"
        if task["kind"] == "verify":
            manifest = {k: acquisition.manifest[k] for k in ("root_name", "files")} if acquisition.manifest else None
            self.finish(task, status="complete", identity=identity, sha256=acquisition.checksum, byte_size=acquisition.byte_size,
                        signature=acquisition.signature, manifest=manifest, readiness=readiness)
            return
        if acquisition.checksum != task["revision"]["sha256"]:
            raise ValueError("Acquisition no longer matches the selected revision. Verify it again")
        run = {"name": path.stem, "experiment_id": task["experiment_id"], "source_class": "vendor" if candidate.kind == "bundle" or candidate.format == "RAW" else "spectrum_list" if candidate.format in {"MGF", "MS2"} else "open"}
        item = QueueItem(task["id"], path, candidate.kind, path.name, candidate.format, acquisition.checksum,
            acquisition.byte_size, acquisition.signature, acquisition.manifest, None, run, "pending",
            self.state.metadata("external-upload:" + task["id"]), 0, 0, None)
        uploader = ResumableUploader(ImportApi(self.api, task["id"]), ImportState(self.state), self.scanner, self.token, self.config.chunk_size_bytes)
        artifact_id, _ = uploader.upload(item)
        self.finish(task, status="complete", identity=identity, artifact_id=artifact_id)

    def finish(self, task, **result):
        return self.call("POST", f"tasks/{task['id']}/result", result)
