"""Loom's operational store (spec §3, §4.2): jobs, per-doc content hashes,
and the correction log. Never Neo4j — see `db/neo4j_client.py` for that door.

This module owns table creation for the pieces specified in spec §6.1, §6.4
(hash tracking, corrections) and §8 (job history, behind `JobStore`).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from typing import Literal

from app.pipeline.types import DocStatus, JobResult, OrphanFlag

_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_hashes (
    source_type TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source_type, doc_id)
);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('create', 'retype', 'delete')),
    rel_type TEXT NOT NULL,
    from_node_id TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    originating_rule_id TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    sinks TEXT NOT NULL,
    config_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    progress REAL NOT NULL DEFAULT 0.0,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Guards every write to `jobs`/`doc_hashes` so two jobs running concurrently
# (both via `JobRunner`'s worker threads) can never interleave writes and
# corrupt each other's rows — SQLite alone only guarantees that within a
# single statement, not across the execute+commit pairs below.
_WRITE_LOCK = threading.Lock()


def connect(db_path: str) -> sqlite3.Connection:
    """Open (creating if needed) the operational store at `db_path`.

    `db_path` may be ":memory:" for tests. Safe to call repeatedly against
    the same file — table creation is idempotent. `check_same_thread=False`
    because `JobRunner` writes progress from the worker thread `Pipeline.run`
    executes in, not the event-loop thread that opened the connection;
    `_WRITE_LOCK` is what keeps that safe rather than SQLite's own checks.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


class HashStore:
    """The `doc_hashes` table (spec §6.1), behind the narrow get/set/delete
    surface `Pipeline.run` needs for incremental re-ingestion.

    A real seam rather than a bare `sqlite3.Connection` passed around: it
    keeps the SQL for "has this doc changed" and "which docs did we
    previously see for this source" in one place, and gives `Pipeline.run`
    something Protocol-shaped it can be tested against instead of `Any`.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_hash(self, source_type: str, doc_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT content_hash FROM doc_hashes WHERE source_type = ? AND doc_id = ?",
            (source_type, doc_id),
        ).fetchone()
        return row[0] if row is not None else None

    def set_hash(self, source_type: str, doc_id: str, content_hash: str, updated_at: str) -> None:
        with _WRITE_LOCK:
            self._conn.execute(
                "INSERT INTO doc_hashes (source_type, doc_id, content_hash, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (source_type, doc_id) DO UPDATE SET "
                "content_hash = excluded.content_hash, updated_at = excluded.updated_at",
                (source_type, doc_id, content_hash, updated_at),
            )
            self._conn.commit()

    def delete_hash(self, source_type: str, doc_id: str) -> None:
        with _WRITE_LOCK:
            self._conn.execute(
                "DELETE FROM doc_hashes WHERE source_type = ? AND doc_id = ?",
                (source_type, doc_id),
            )
            self._conn.commit()

    def doc_ids_for_source(self, source_type: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT doc_id FROM doc_hashes WHERE source_type = ?",
            (source_type,),
        ).fetchall()
        return {row[0] for row in rows}


JobStatusValue = Literal["pending", "running", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class JobRow:
    """One `jobs` row, as read back by `JobStore` — `result` is `None`
    until the job has processed at least one doc (see `record_progress`)
    or reached a terminal status (see `complete_job`)."""

    id: str
    source_type: str
    source_path: str
    sinks: list[str]
    config_id: str
    status: JobStatusValue
    progress: float
    result: JobResult | None
    error: str | None
    created_at: str
    updated_at: str


def _serialize_result(result: JobResult) -> str:
    return json.dumps(
        {
            "doc_statuses": [asdict(s) for s in result.doc_statuses],
            "orphans": [asdict(o) for o in result.orphans],
        }
    )


def _deserialize_result(raw: str) -> JobResult:
    data = json.loads(raw)
    return JobResult(
        doc_statuses=[DocStatus(**s) for s in data["doc_statuses"]],
        orphans=[OrphanFlag(**o) for o in data["orphans"]],
    )


class JobStore:
    """The `jobs` table (spec §8), behind the narrow create/update/read
    surface the Jobs API and `JobRunner` need — mirrors `HashStore`'s shape
    so job history gets the same Protocol-able seam instead of raw SQL
    scattered across the API layer.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_job(
        self,
        job_id: str,
        source_type: str,
        source_path: str,
        sinks: list[str],
        config_id: str,
        created_at: str,
    ) -> None:
        with _WRITE_LOCK:
            self._conn.execute(
                "INSERT INTO jobs "
                "(id, source_type, source_path, sinks, config_id, status, progress, "
                "result, error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', 0.0, NULL, NULL, ?, ?)",
                (job_id, source_type, source_path, json.dumps(sinks), config_id, created_at, created_at),
            )
            self._conn.commit()

    def mark_running(self, job_id: str, updated_at: str) -> None:
        with _WRITE_LOCK:
            self._conn.execute(
                "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?",
                (updated_at, job_id),
            )
            self._conn.commit()

    def record_progress(self, job_id: str, progress: float, updated_at: str) -> None:
        with _WRITE_LOCK:
            self._conn.execute(
                "UPDATE jobs SET progress = ?, updated_at = ? WHERE id = ?",
                (progress, updated_at, job_id),
            )
            self._conn.commit()

    def complete_job(
        self, job_id: str, status: JobStatusValue, result: JobResult, updated_at: str
    ) -> None:
        """Terminal write for a run that finished (`completed`) or was
        stopped via `should_cancel` (`cancelled`). Progress is left as
        whatever the last `record_progress` call set — a cancelled job's
        progress legitimately stays below 1.0, reflecting where it stopped.
        """
        with _WRITE_LOCK:
            self._conn.execute(
                "UPDATE jobs SET status = ?, result = ?, updated_at = ? WHERE id = ?",
                (status, _serialize_result(result), updated_at, job_id),
            )
            self._conn.commit()

    def fail_job(self, job_id: str, error: str, updated_at: str) -> None:
        """Terminal write for a run that raised outside `Pipeline.run`'s own
        per-doc error handling (e.g. an unresolvable source/sink/config) —
        distinct from a per-doc "failed" `DocStatus`, which is a normal
        outcome captured in `result` via `complete_job` instead."""
        with _WRITE_LOCK:
            self._conn.execute(
                "UPDATE jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                (error, updated_at, job_id),
            )
            self._conn.commit()

    def get_job(self, job_id: str) -> JobRow | None:
        row = self._conn.execute(
            "SELECT id, source_type, source_path, sinks, config_id, status, progress, "
            "result, error, created_at, updated_at FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        return self._row_to_job(row) if row is not None else None

    def list_jobs(self, limit: int = 20, offset: int = 0) -> tuple[list[JobRow], int]:
        """Most-recent-first job history (spec §8), with a total count so
        callers can build pagination metadata without a second round trip."""
        total = self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        rows = self._conn.execute(
            "SELECT id, source_type, source_path, sinks, config_id, status, progress, "
            "result, error, created_at, updated_at FROM jobs "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._row_to_job(row) for row in rows], total

    @staticmethod
    def _row_to_job(row: tuple[object, ...]) -> JobRow:
        (
            job_id,
            source_type,
            source_path,
            sinks_json,
            config_id,
            status,
            progress,
            result_json,
            error,
            created_at,
            updated_at,
        ) = row
        assert isinstance(job_id, str)
        assert isinstance(source_type, str)
        assert isinstance(source_path, str)
        assert isinstance(sinks_json, str)
        assert isinstance(config_id, str)
        assert isinstance(status, str)
        assert isinstance(progress, float)
        assert result_json is None or isinstance(result_json, str)
        assert error is None or isinstance(error, str)
        assert isinstance(created_at, str)
        assert isinstance(updated_at, str)
        return JobRow(
            id=job_id,
            source_type=source_type,
            source_path=source_path,
            sinks=json.loads(sinks_json),
            config_id=config_id,
            status=status,  # type: ignore[arg-type]
            progress=progress,
            result=_deserialize_result(result_json) if result_json is not None else None,
            error=error,
            created_at=created_at,
            updated_at=updated_at,
        )
