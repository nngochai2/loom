"""Loom's operational store (spec §3, §4.2): instances, jobs, per-doc content
hashes, and the correction log. Never Neo4j — see `db/neo4j_client.py` for
that door.

This module owns table creation for the pieces specified in spec §6.1, §6.4
(hash tracking, corrections), §6.6/ADR-0025 (instance catalog, behind
`InstanceStore`) and §8 (job history, behind `JobStore`).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from app.pipeline.types import DocStatus, ExtractionVersion, JobResult, OrphanFlag


def now_iso() -> str:
    """The single `created_at`/`updated_at` timestamp format every writer in
    this module (and the API routers built on it) uses — shared so
    `JobRunner` and the Instances API don't each keep their own copy."""
    return datetime.now(UTC).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_hashes (
    source_type TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    prompt_version TEXT,
    model TEXT,
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

CREATE TABLE IF NOT EXISTS instances (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    sinks TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_type, source_path, sinks)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
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

    def set_hash(
        self,
        source_type: str,
        doc_id: str,
        content_hash: str,
        updated_at: str,
        *,
        prompt_version: str | None = None,
        model: str | None = None,
    ) -> None:
        """`prompt_version`/`model` are the LLM re-extraction trigger fields
        (ADR-0020) — optional and `None` for sources/docs with no prose-
        extraction concept (the vast majority of calls), so every existing
        caller is unaffected."""
        with _WRITE_LOCK:
            self._conn.execute(
                "INSERT INTO doc_hashes "
                "(source_type, doc_id, content_hash, prompt_version, model, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (source_type, doc_id) DO UPDATE SET "
                "content_hash = excluded.content_hash, "
                "prompt_version = excluded.prompt_version, "
                "model = excluded.model, "
                "updated_at = excluded.updated_at",
                (source_type, doc_id, content_hash, prompt_version, model, updated_at),
            )
            self._conn.commit()

    def get_extraction_version(self, source_type: str, doc_id: str) -> ExtractionVersion | None:
        """The fingerprint a doc's current LLM-derived extractions came from
        (ADR-0020), or `None` if the doc is unseen or was last written by a
        run with no prose-extraction concept. Returns the same
        `ExtractionVersion` type `Pipeline.run` is handed, so callers
        compare the two directly instead of re-deriving a tuple."""
        row = self._conn.execute(
            "SELECT prompt_version, model FROM doc_hashes WHERE source_type = ? AND doc_id = ?",
            (source_type, doc_id),
        ).fetchone()
        if row is None or row[0] is None or row[1] is None:
            return None
        return ExtractionVersion(prompt_version=row[0], model=row[1])

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


class DuplicateInstanceError(Exception):
    """Raised by `InstanceStore.create_instance` when an instance with the
    identical (source_type, source_path, sinks) tuple already exists
    (ADR-0025) — the API layer turns this into a 409."""


@dataclass(frozen=True)
class InstanceRow:
    """One `instances` row (ADR-0025), plus the job-history summary fields
    `list_instances`/`get_instance` compute alongside it so the Instances
    page (spec §9) doesn't need a second round trip per row."""

    id: str
    name: str
    source_type: str
    source_path: str
    sinks: list[str]
    created_at: str
    updated_at: str
    job_count: int
    last_status: JobStatusValue | None
    last_run_at: str | None


# Shared by `get_instance`/`list_instances`: each instance's job count and
# its most recent job's status/timestamp, computed the same way in both so
# a row read singly and a row read as part of a list never disagree.
_INSTANCE_JOB_SUMMARY_COLUMNS = """
    (SELECT COUNT(*) FROM jobs j WHERE j.instance_id = i.id) AS job_count,
    (SELECT j.status FROM jobs j WHERE j.instance_id = i.id
     ORDER BY j.created_at DESC, j.id DESC LIMIT 1) AS last_status,
    (SELECT j.created_at FROM jobs j WHERE j.instance_id = i.id
     ORDER BY j.created_at DESC, j.id DESC LIMIT 1) AS last_run_at
"""


class InstanceStore:
    """The `instances` table (ADR-0025) — a catalog of source+sink recipes,
    never a partition of the graph itself (see spec §6.6). Deleting an
    instance removes only this bookkeeping and its `jobs` rows (via the
    `ON DELETE CASCADE` on `jobs.instance_id`); it never touches
    Neo4j/Chroma."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_instance(
        self,
        instance_id: str,
        name: str,
        source_type: str,
        source_path: str,
        sinks: list[str],
        created_at: str,
    ) -> None:
        sinks_json = json.dumps(sorted(sinks))
        with _WRITE_LOCK:
            try:
                self._conn.execute(
                    "INSERT INTO instances "
                    "(id, name, source_type, source_path, sinks, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (instance_id, name, source_type, source_path, sinks_json, created_at, created_at),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                if "UNIQUE" in str(exc):
                    raise DuplicateInstanceError(
                        f"An instance for {source_type!r} at {source_path!r} with "
                        f"sinks {sorted(sinks)!r} already exists"
                    ) from exc
                raise

    def get_instance(self, instance_id: str) -> InstanceRow | None:
        row = self._conn.execute(
            "SELECT i.id, i.name, i.source_type, i.source_path, i.sinks, "
            f"i.created_at, i.updated_at, {_INSTANCE_JOB_SUMMARY_COLUMNS} "
            "FROM instances i WHERE i.id = ?",
            (instance_id,),
        ).fetchone()
        return self._row_to_instance(row) if row is not None else None

    def list_instances(self) -> list[InstanceRow]:
        """Most-recently-run first (spec §9); instances with no runs yet
        sort by `created_at` instead, since they have no run to sort by."""
        rows = self._conn.execute(
            "SELECT i.id, i.name, i.source_type, i.source_path, i.sinks, "
            f"i.created_at, i.updated_at, {_INSTANCE_JOB_SUMMARY_COLUMNS} "
            "FROM instances i "
            "ORDER BY COALESCE(last_run_at, i.created_at) DESC, i.id DESC"
        ).fetchall()
        return [self._row_to_instance(row) for row in rows]

    def rename_instance(self, instance_id: str, name: str, updated_at: str) -> None:
        with _WRITE_LOCK:
            self._conn.execute(
                "UPDATE instances SET name = ?, updated_at = ? WHERE id = ?",
                (name, updated_at, instance_id),
            )
            self._conn.commit()

    def delete_instance(self, instance_id: str) -> None:
        """Catalog-only (ADR-0025): removes this instance's bookkeeping row.
        Its `jobs` history rows go with it via `ON DELETE CASCADE` on
        `jobs.instance_id` (enforced — `connect()` sets `PRAGMA foreign_keys
        = ON`), not a second manual delete here. Never issues a Neo4j/Chroma
        write or delete — the graph/vector data those jobs wrote is left
        exactly as-is, untagged and unattributed, same as any orphaned
        content."""
        with _WRITE_LOCK:
            self._conn.execute("DELETE FROM instances WHERE id = ?", (instance_id,))
            self._conn.commit()

    @staticmethod
    def _row_to_instance(row: tuple[object, ...]) -> InstanceRow:
        (
            instance_id,
            name,
            source_type,
            source_path,
            sinks_json,
            created_at,
            updated_at,
            job_count,
            last_status,
            last_run_at,
        ) = row
        assert isinstance(instance_id, str)
        assert isinstance(name, str)
        assert isinstance(source_type, str)
        assert isinstance(source_path, str)
        assert isinstance(sinks_json, str)
        assert isinstance(created_at, str)
        assert isinstance(updated_at, str)
        assert isinstance(job_count, int)
        assert last_status is None or isinstance(last_status, str)
        assert last_run_at is None or isinstance(last_run_at, str)
        return InstanceRow(
            id=instance_id,
            name=name,
            source_type=source_type,
            source_path=source_path,
            sinks=json.loads(sinks_json),
            created_at=created_at,
            updated_at=updated_at,
            job_count=job_count,
            last_status=last_status,  # type: ignore[arg-type]
            last_run_at=last_run_at,
        )


JobStatusValue = Literal["pending", "running", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class JobRow:
    """One `jobs` row, as read back by `JobStore` — `result` is `None`
    until the job has processed at least one doc (see `record_progress`)
    or reached a terminal status (see `complete_job`)."""

    id: str
    instance_id: str
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
        instance_id: str,
        source_type: str,
        source_path: str,
        sinks: list[str],
        config_id: str,
        created_at: str,
    ) -> None:
        """`instance_id` (ADR-0025) is the instance this job belongs to —
        `source_type`/`source_path`/`sinks` are still passed explicitly
        (rather than looked up from the instance here) so `JobRunner`/tests
        that only care about run mechanics don't need an `InstanceStore`
        round trip; the Jobs API resolves them from the instance before
        calling this."""
        with _WRITE_LOCK:
            self._conn.execute(
                "INSERT INTO jobs "
                "(id, instance_id, source_type, source_path, sinks, config_id, status, progress, "
                "result, error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', 0.0, NULL, NULL, ?, ?)",
                (
                    job_id,
                    instance_id,
                    source_type,
                    source_path,
                    json.dumps(sorted(sinks)),  # same canonical form InstanceStore uses
                    config_id,
                    created_at,
                    created_at,
                ),
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
            "SELECT id, instance_id, source_type, source_path, sinks, config_id, status, "
            "progress, result, error, created_at, updated_at FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        return self._row_to_job(row) if row is not None else None

    def list_jobs(
        self, limit: int = 20, offset: int = 0, instance_id: str | None = None
    ) -> tuple[list[JobRow], int]:
        """Most-recent-first job history (spec §8), with a total count so
        callers can build pagination metadata without a second round trip.
        `instance_id` (ADR-0026) scopes history to one instance, for the
        Instances detail page."""
        where = " WHERE instance_id = ?" if instance_id is not None else ""
        params = (instance_id,) if instance_id is not None else ()
        total = self._conn.execute(f"SELECT COUNT(*) FROM jobs{where}", params).fetchone()[0]
        rows = self._conn.execute(
            "SELECT id, instance_id, source_type, source_path, sinks, config_id, status, "
            f"progress, result, error, created_at, updated_at FROM jobs{where} "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [self._row_to_job(row) for row in rows], total

    @staticmethod
    def _row_to_job(row: tuple[object, ...]) -> JobRow:
        (
            job_id,
            instance_id,
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
        assert isinstance(instance_id, str)
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
            instance_id=instance_id,
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
