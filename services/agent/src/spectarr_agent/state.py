"""Durable SQLite state for discovery and offline uploads."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .discovery import HashedAcquisition


@dataclass(frozen=True)
class QueueItem:
    id: str
    source_path: Path
    source_kind: str
    source_name: str
    format: str
    checksum: str
    byte_size: int
    signature: str
    manifest: dict | None
    run_id: str | None
    run: dict | None
    status: str
    upload_id: str | None
    attempts: int
    next_attempt_at: float
    last_error: str | None


class AgentState:
    """Crash-safe queue and stability history stored in SQLite."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.connection = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._migrate()
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self.recover_interrupted()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS observations (
                source_path TEXT PRIMARY KEY,
                signature TEXT NOT NULL,
                stable_since REAL NOT NULL,
                last_seen REAL NOT NULL,
                last_enqueued_signature TEXT,
                last_checksum TEXT,
                blocked_reason TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS upload_queue (
                id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                source_kind TEXT NOT NULL CHECK (source_kind IN ('file', 'bundle')),
                source_name TEXT NOT NULL,
                format TEXT NOT NULL,
                checksum TEXT NOT NULL UNIQUE,
                byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
                signature TEXT NOT NULL,
                manifest_json TEXT,
                run_id TEXT,
                run_json TEXT,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'uploading', 'retry', 'complete', 'deduplicated', 'failed')
                ),
                upload_id TEXT,
                artifact_id TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_upload_queue_ready
                ON upload_queue(status, next_attempt_at, created_at)
            """,
        )
        for statement in statements:
            self.connection.execute(statement)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def metadata(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_metadata(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def clear_registration(self) -> None:
        self.connection.execute("DELETE FROM metadata WHERE key IN ('agent_id', 'agent_token')")
        self.connection.execute(
            """
            UPDATE upload_queue
            SET status = 'retry', next_attempt_at = 0, updated_at = ?
            WHERE status = 'failed'
              AND (last_error LIKE 'Spectarr API returned 401:%'
                   OR last_error LIKE 'Spectarr API returned 403:%')
            """,
            (time.time(),),
        )

    def local_agent_id(self) -> str:
        value = self.metadata("local_agent_id")
        if value:
            return value
        value = str(uuid.uuid4())
        self.set_metadata("local_agent_id", value)
        return value

    def observe(
        self,
        source_path: Path,
        signature: str,
        now: float,
        stability_seconds: float,
        blocked_reason: str | None = None,
    ) -> bool:
        key = str(source_path)
        row = self.connection.execute(
            "SELECT signature, stable_since FROM observations WHERE source_path = ?", (key,)
        ).fetchone()
        stable_since = now
        if row and row["signature"] == signature and blocked_reason is None:
            stable_since = float(row["stable_since"])
        self.connection.execute(
            """
            INSERT INTO observations(source_path, signature, stable_since, last_seen, blocked_reason)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_path) DO UPDATE SET
                signature = excluded.signature,
                stable_since = excluded.stable_since,
                last_seen = excluded.last_seen,
                blocked_reason = excluded.blocked_reason
            """,
            (key, signature, stable_since, now, blocked_reason),
        )
        return blocked_reason is None and now - stable_since >= stability_seconds

    def needs_hashing(self, source_path: Path, signature: str) -> bool:
        row = self.connection.execute(
            "SELECT last_enqueued_signature FROM observations WHERE source_path = ?", (str(source_path),)
        ).fetchone()
        return row is None or row["last_enqueued_signature"] != signature

    def mark_observation_enqueued(self, source_path: Path, signature: str, checksum: str) -> None:
        self.connection.execute(
            """
            UPDATE observations
            SET last_enqueued_signature = ?, last_checksum = ?
            WHERE source_path = ?
            """,
            (signature, checksum, str(source_path)),
        )

    def remove_stale_observations(self, seen_after: float) -> None:
        self.connection.execute("DELETE FROM observations WHERE last_seen < ?", (seen_after,))

    def enqueue(
        self,
        acquisition: HashedAcquisition,
        *,
        run_id: str | None = None,
        run: dict | None = None,
        now: float | None = None,
    ) -> bool:
        if bool(run_id) == bool(run):
            raise ValueError("Queue items require exactly one run target")
        created = time.time() if now is None else now
        queue_id = str(uuid.uuid4())
        inserted = False
        with self.transaction():
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO upload_queue(
                    id, source_path, source_kind, source_name, format, checksum, byte_size,
                    signature, manifest_json, run_id, run_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    queue_id,
                    str(acquisition.path),
                    acquisition.kind,
                    acquisition.path.name,
                    acquisition.format,
                    acquisition.checksum,
                    acquisition.byte_size,
                    acquisition.signature,
                    json.dumps(acquisition.manifest, sort_keys=True) if acquisition.manifest else None,
                    run_id,
                    json.dumps(run, sort_keys=True) if run else None,
                    created,
                    created,
                ),
            )
            inserted = cursor.rowcount == 1
            if not inserted:
                existing = self.connection.execute(
                    "SELECT id, status FROM upload_queue WHERE checksum = ?", (acquisition.checksum,)
                ).fetchone()
                if existing and existing["status"] == "failed":
                    self.connection.execute(
                        """
                        UPDATE upload_queue SET
                            source_path = ?, source_kind = ?, source_name = ?, format = ?,
                            byte_size = ?, signature = ?, manifest_json = ?, run_id = ?, run_json = ?,
                            status = 'pending', upload_id = NULL, artifact_id = NULL, attempts = 0,
                            next_attempt_at = 0, last_error = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            str(acquisition.path),
                            acquisition.kind,
                            acquisition.path.name,
                            acquisition.format,
                            acquisition.byte_size,
                            acquisition.signature,
                            json.dumps(acquisition.manifest, sort_keys=True) if acquisition.manifest else None,
                            run_id,
                            json.dumps(run, sort_keys=True) if run else None,
                            created,
                            existing["id"],
                        ),
                    )
                    inserted = True
            self.mark_observation_enqueued(acquisition.path, acquisition.signature, acquisition.checksum)
        return inserted

    def recover_interrupted(self) -> None:
        now = time.time()
        self.connection.execute(
            """
            UPDATE upload_queue
            SET status = 'retry', next_attempt_at = ?,
                last_error = COALESCE(last_error, 'Agent stopped during upload'), updated_at = ?
            WHERE status = 'uploading'
            """,
            (now, now),
        )

    def claim_next(self, now: float | None = None, max_attempts: int = 0) -> QueueItem | None:
        claimed_at = time.time() if now is None else now
        with self.transaction():
            attempt_clause = "" if max_attempts == 0 else "AND attempts < ?"
            parameters: tuple[object, ...] = (claimed_at,) if max_attempts == 0 else (claimed_at, max_attempts)
            row = self.connection.execute(
                f"""
                SELECT * FROM upload_queue
                WHERE status IN ('pending', 'retry') AND next_attempt_at <= ? {attempt_clause}
                ORDER BY created_at, id LIMIT 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                return None
            self.connection.execute(
                """
                UPDATE upload_queue
                SET status = 'uploading', attempts = attempts + 1, updated_at = ?
                WHERE id = ?
                """,
                (claimed_at, row["id"]),
            )
            row = self.connection.execute("SELECT * FROM upload_queue WHERE id = ?", (row["id"],)).fetchone()
        return self._queue_item(row)

    def set_upload_id(self, queue_id: str, upload_id: str) -> None:
        self.connection.execute(
            "UPDATE upload_queue SET upload_id = ?, updated_at = ? WHERE id = ?",
            (upload_id, time.time(), queue_id),
        )

    def clear_upload_id(self, queue_id: str) -> None:
        self.connection.execute(
            "UPDATE upload_queue SET upload_id = NULL, updated_at = ? WHERE id = ?",
            (time.time(), queue_id),
        )

    def complete(self, queue_id: str, artifact_id: str | None, deduplicated: bool = False) -> None:
        status = "deduplicated" if deduplicated else "complete"
        self.connection.execute(
            """
            UPDATE upload_queue
            SET status = ?, artifact_id = ?, last_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (status, artifact_id, time.time(), queue_id),
        )

    def retry(
        self,
        queue_id: str,
        error: str,
        delay_seconds: float,
        permanent: bool = False,
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else now
        status = "failed" if permanent else "retry"
        self.connection.execute(
            """
            UPDATE upload_queue
            SET status = ?, next_attempt_at = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now + max(0, delay_seconds), error[:10000], now, queue_id),
        )

    def counts(self) -> dict[str, int]:
        result = {str(row["status"]): int(row["count"]) for row in self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM upload_queue GROUP BY status"
        )}
        return result

    def get(self, queue_id: str) -> QueueItem | None:
        row = self.connection.execute("SELECT * FROM upload_queue WHERE id = ?", (queue_id,)).fetchone()
        return self._queue_item(row) if row else None

    @staticmethod
    def _queue_item(row: sqlite3.Row) -> QueueItem:
        return QueueItem(
            id=str(row["id"]),
            source_path=Path(str(row["source_path"])),
            source_kind=str(row["source_kind"]),
            source_name=str(row["source_name"]),
            format=str(row["format"]),
            checksum=str(row["checksum"]),
            byte_size=int(row["byte_size"]),
            signature=str(row["signature"]),
            manifest=json.loads(row["manifest_json"]) if row["manifest_json"] else None,
            run_id=str(row["run_id"]) if row["run_id"] else None,
            run=json.loads(row["run_json"]) if row["run_json"] else None,
            status=str(row["status"]),
            upload_id=str(row["upload_id"]) if row["upload_id"] else None,
            attempts=int(row["attempts"]),
            next_attempt_at=float(row["next_attempt_at"]),
            last_error=str(row["last_error"]) if row["last_error"] else None,
        )
