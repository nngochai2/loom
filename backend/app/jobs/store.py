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
