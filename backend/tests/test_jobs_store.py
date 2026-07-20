import sqlite3

import pytest

from app.jobs.store import HashStore, connect


@pytest.fixture()
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


def test_connect_creates_doc_hashes_table(conn: sqlite3.Connection):
    conn.execute(
        "INSERT INTO doc_hashes (source_type, doc_id, content_hash, updated_at) "
        "VALUES (?, ?, ?, ?)",
        ("obsidian", "note1.md", "hash-abc", "2026-07-20T00:00:00Z"),
    )
    conn.commit()

    row = conn.execute(
        "SELECT source_type, doc_id, content_hash, updated_at FROM doc_hashes "
        "WHERE doc_id = ?",
        ("note1.md",),
    ).fetchone()

    assert row == ("obsidian", "note1.md", "hash-abc", "2026-07-20T00:00:00Z")


def test_doc_hashes_primary_key_is_source_type_and_doc_id(conn: sqlite3.Connection):
    conn.execute(
        "INSERT INTO doc_hashes (source_type, doc_id, content_hash, updated_at) "
        "VALUES ('obsidian', 'note1.md', 'hash-1', 't1')"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO doc_hashes (source_type, doc_id, content_hash, updated_at) "
            "VALUES ('obsidian', 'note1.md', 'hash-2', 't2')"
        )


def test_connect_creates_corrections_table(conn: sqlite3.Connection):
    conn.execute(
        "INSERT INTO corrections "
        "(timestamp, action, rel_type, from_node_id, to_node_id, originating_rule_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("2026-07-20T00:00:00Z", "delete", "DEPENDS_ON", "n1", "n2", "rule-42"),
    )
    conn.commit()

    row = conn.execute(
        "SELECT action, rel_type, from_node_id, to_node_id, originating_rule_id "
        "FROM corrections"
    ).fetchone()

    assert row == ("delete", "DEPENDS_ON", "n1", "n2", "rule-42")


def test_corrections_originating_rule_id_is_nullable(conn: sqlite3.Connection):
    conn.execute(
        "INSERT INTO corrections (timestamp, action, rel_type, from_node_id, to_node_id) "
        "VALUES ('t', 'create', 'USES', 'a', 'b')"
    )
    conn.commit()

    row = conn.execute(
        "SELECT originating_rule_id FROM corrections"
    ).fetchone()

    assert row == (None,)


def test_corrections_action_is_constrained_to_known_values(conn: sqlite3.Connection):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO corrections (timestamp, action, rel_type, from_node_id, to_node_id) "
            "VALUES ('t', 'not-a-real-action', 'USES', 'a', 'b')"
        )


def test_connect_is_idempotent_across_repeated_calls(tmp_path):
    db_path = tmp_path / "loom.sqlite3"
    connect(str(db_path)).close()
    conn2 = connect(str(db_path))  # must not error re-creating existing tables
    conn2.execute(
        "INSERT INTO doc_hashes (source_type, doc_id, content_hash, updated_at) "
        "VALUES ('docx', 'a.docx', 'h', 't')"
    )
    conn2.commit()
    conn2.close()


# --- HashStore: the narrow seam Pipeline.run uses for incremental re-ingestion
# (spec §6.1) instead of holding a bare sqlite3.Connection + raw SQL itself. ---


def test_hash_store_get_hash_returns_none_for_unseen_doc(conn: sqlite3.Connection):
    store = HashStore(conn)

    assert store.get_hash("obsidian", "never-seen.md") is None


def test_hash_store_set_then_get_round_trips(conn: sqlite3.Connection):
    store = HashStore(conn)

    store.set_hash("obsidian", "note1.md", "hash-abc", "2026-07-20T00:00:00Z")

    assert store.get_hash("obsidian", "note1.md") == "hash-abc"


def test_hash_store_set_hash_upserts_on_repeated_calls(conn: sqlite3.Connection):
    store = HashStore(conn)

    store.set_hash("obsidian", "note1.md", "hash-1", "t1")
    store.set_hash("obsidian", "note1.md", "hash-2", "t2")

    assert store.get_hash("obsidian", "note1.md") == "hash-2"
    rows = conn.execute("SELECT COUNT(*) FROM doc_hashes").fetchone()
    assert rows[0] == 1  # no duplicate row under the same (source_type, doc_id)


def test_hash_store_scopes_get_hash_by_source_type(conn: sqlite3.Connection):
    store = HashStore(conn)
    store.set_hash("obsidian", "shared-id", "hash-obsidian", "t")
    store.set_hash("docx", "shared-id", "hash-docx", "t")

    assert store.get_hash("obsidian", "shared-id") == "hash-obsidian"
    assert store.get_hash("docx", "shared-id") == "hash-docx"


def test_hash_store_delete_hash_removes_the_row(conn: sqlite3.Connection):
    store = HashStore(conn)
    store.set_hash("obsidian", "note1.md", "hash-abc", "t")

    store.delete_hash("obsidian", "note1.md")

    assert store.get_hash("obsidian", "note1.md") is None


def test_hash_store_delete_hash_on_unseen_doc_is_a_no_op(conn: sqlite3.Connection):
    store = HashStore(conn)

    store.delete_hash("obsidian", "never-seen.md")  # must not raise


def test_hash_store_doc_ids_for_source_returns_only_that_sources_ids(conn: sqlite3.Connection):
    store = HashStore(conn)
    store.set_hash("obsidian", "a.md", "h1", "t")
    store.set_hash("obsidian", "b.md", "h2", "t")
    store.set_hash("docx", "c.docx", "h3", "t")

    assert store.doc_ids_for_source("obsidian") == {"a.md", "b.md"}
    assert store.doc_ids_for_source("docx") == {"c.docx"}
