"""Loom's operational store (spec §3, §4.2): jobs, per-doc content hashes,
and the correction log. Never Neo4j — see `db/neo4j_client.py` for that door.

This module owns table creation for the pieces already specified in
spec §6.1 and §6.4 (hash tracking, corrections). Job-history tables land
with the Jobs API ticket, in this same module.
"""

from __future__ import annotations

import sqlite3

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
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open (creating if needed) the operational store at `db_path`.

    `db_path` may be ":memory:" for tests. Safe to call repeatedly against
    the same file — table creation is idempotent.
    """
    conn = sqlite3.connect(db_path)
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
        self._conn.execute(
            "INSERT INTO doc_hashes (source_type, doc_id, content_hash, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (source_type, doc_id) DO UPDATE SET "
            "content_hash = excluded.content_hash, updated_at = excluded.updated_at",
            (source_type, doc_id, content_hash, updated_at),
        )
        self._conn.commit()

    def delete_hash(self, source_type: str, doc_id: str) -> None:
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
