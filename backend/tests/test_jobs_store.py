import sqlite3

import pytest

from app.jobs.store import HashStore, InstanceStore, JobStore, connect
from app.pipeline.types import DocStatus, ExtractionVersion, JobResult, OrphanFlag


@pytest.fixture()
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture()
def instance_id(conn: sqlite3.Connection) -> str:
    """A default instance (ADR-0025) satisfying `jobs.instance_id`'s FK —
    JobStore tests are about job mechanics, not instance bookkeeping, so
    every job in this file is free to reuse this one regardless of the
    source_type/source_path it's created with."""
    InstanceStore(conn).create_instance("inst1", "Test instance", "obsidian", "/vault", ["neo4j"], "t0")
    return "inst1"


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


# --- prompt_version/model tracking (ADR-0020, issue #19): extends the same
# doc_hashes row set_hash already writes, rather than a second table. ---


def test_hash_store_get_extraction_version_returns_none_for_unseen_doc(conn: sqlite3.Connection):
    store = HashStore(conn)

    assert store.get_extraction_version("docx", "never-seen.docx") is None


def test_hash_store_get_extraction_version_returns_none_when_set_hash_omitted_it(
    conn: sqlite3.Connection,
):
    store = HashStore(conn)
    store.set_hash("obsidian", "note1.md", "hash-abc", "t")  # no prompt_version/model kwargs

    assert store.get_extraction_version("obsidian", "note1.md") is None


def test_hash_store_set_hash_and_get_extraction_version_round_trip(conn: sqlite3.Connection):
    store = HashStore(conn)

    store.set_hash("docx", "doc1.docx", "hash-abc", "t", prompt_version="2", model="llama3.1")

    assert store.get_extraction_version("docx", "doc1.docx") == ExtractionVersion(
        prompt_version="2", model="llama3.1"
    )
    assert store.get_hash("docx", "doc1.docx") == "hash-abc"  # unaffected


def test_hash_store_set_hash_upserts_extraction_version_on_repeated_calls(
    conn: sqlite3.Connection,
):
    store = HashStore(conn)
    store.set_hash("docx", "doc1.docx", "hash-abc", "t1", prompt_version="1", model="llama3.1")

    store.set_hash("docx", "doc1.docx", "hash-abc", "t2", prompt_version="2", model="llama3.2")

    assert store.get_extraction_version("docx", "doc1.docx") == ExtractionVersion(
        prompt_version="2", model="llama3.2"
    )


def test_hash_store_set_hash_can_clear_a_previously_recorded_extraction_version(
    conn: sqlite3.Connection,
):
    # Re-ingesting via a config that no longer enables prose extraction
    # writes prompt_version/model back to NULL for that doc.
    store = HashStore(conn)
    store.set_hash("docx", "doc1.docx", "hash-abc", "t1", prompt_version="1", model="llama3.1")

    store.set_hash("docx", "doc1.docx", "hash-abc", "t2")

    assert store.get_extraction_version("docx", "doc1.docx") is None


# --- JobStore: the jobs table (spec §8), behind the same narrow-seam
# pattern HashStore uses. ---


def test_connect_creates_jobs_table_with_a_pending_row(conn: sqlite3.Connection, instance_id: str):
    conn.execute(
        "INSERT INTO jobs (id, instance_id, source_type, source_path, sinks, config_id, status, "
        "progress, result, error, created_at, updated_at) "
        "VALUES ('job1', ?, 'obsidian', '/vault', '[\"neo4j\"]', 'default.yml', 'pending', "
        "0.0, NULL, NULL, 't0', 't0')",
        (instance_id,),
    )
    conn.commit()

    row = conn.execute("SELECT status, progress FROM jobs WHERE id = 'job1'").fetchone()
    assert row == ("pending", 0.0)


def test_jobs_status_is_constrained_to_known_values(conn: sqlite3.Connection, instance_id: str):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (id, instance_id, source_type, source_path, sinks, config_id, status, "
            "progress, result, error, created_at, updated_at) "
            "VALUES ('job1', ?, 'obsidian', '/vault', '[]', 'default.yml', 'not-a-status', "
            "0.0, NULL, NULL, 't0', 't0')",
            (instance_id,),
        )


def test_jobs_instance_id_must_reference_an_existing_instance(conn: sqlite3.Connection):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (id, instance_id, source_type, source_path, sinks, config_id, status, "
            "progress, result, error, created_at, updated_at) "
            "VALUES ('job1', 'does-not-exist', 'obsidian', '/vault', '[]', 'default.yml', "
            "'pending', 0.0, NULL, NULL, 't0', 't0')"
        )


def test_job_store_create_job_starts_pending_with_zero_progress(
    conn: sqlite3.Connection, instance_id: str
):
    store = JobStore(conn)

    store.create_job("job1", instance_id, "obsidian", "/vault", ["neo4j"], "default.yml", "t0")
    row = store.get_job("job1")

    assert row is not None
    assert row.instance_id == instance_id
    assert row.status == "pending"
    assert row.progress == 0.0
    assert row.sinks == ["neo4j"]
    assert row.result is None
    assert row.error is None


def test_job_store_get_job_returns_none_for_unknown_id(conn: sqlite3.Connection):
    store = JobStore(conn)

    assert store.get_job("never-created") is None


def test_job_store_mark_running_updates_status(conn: sqlite3.Connection, instance_id: str):
    store = JobStore(conn)
    store.create_job("job1", instance_id, "obsidian", "/vault", ["neo4j"], "default.yml", "t0")

    store.mark_running("job1", "t1")

    row = store.get_job("job1")
    assert row is not None
    assert row.status == "running"
    assert row.updated_at == "t1"


def test_job_store_record_progress_updates_progress_and_leaves_status_alone(
    conn: sqlite3.Connection, instance_id: str
):
    store = JobStore(conn)
    store.create_job("job1", instance_id, "obsidian", "/vault", ["neo4j"], "default.yml", "t0")
    store.mark_running("job1", "t1")

    store.record_progress("job1", 0.5, "t2")

    row = store.get_job("job1")
    assert row is not None
    assert row.status == "running"
    assert row.progress == 0.5


def test_job_store_complete_job_stores_status_and_result(
    conn: sqlite3.Connection, instance_id: str
):
    store = JobStore(conn)
    store.create_job("job1", instance_id, "obsidian", "/vault", ["neo4j"], "default.yml", "t0")
    result = JobResult(
        doc_statuses=[DocStatus("a.md", "updated"), DocStatus("b.md", "failed", "boom")],
        orphans=[OrphanFlag(edge_id="4:x:1", reason="endpoint gone")],
    )

    store.complete_job("job1", "completed", result, "t2")

    row = store.get_job("job1")
    assert row is not None
    assert row.status == "completed"
    assert row.result == result


def test_job_store_complete_job_leaves_progress_as_last_recorded_value(
    conn: sqlite3.Connection, instance_id: str
):
    # A cancelled job's progress should reflect where it stopped, not jump
    # to 1.0 just because the job reached a terminal status.
    store = JobStore(conn)
    store.create_job("job1", instance_id, "obsidian", "/vault", ["neo4j"], "default.yml", "t0")
    store.record_progress("job1", 0.33, "t1")

    store.complete_job("job1", "cancelled", JobResult(), "t2")

    row = store.get_job("job1")
    assert row is not None
    assert row.status == "cancelled"
    assert row.progress == 0.33


def test_job_store_fail_job_records_error_and_status(conn: sqlite3.Connection, instance_id: str):
    store = JobStore(conn)
    store.create_job("job1", instance_id, "obsidian", "/vault", ["neo4j"], "default.yml", "t0")

    store.fail_job("job1", "config file not found", "t3")

    row = store.get_job("job1")
    assert row is not None
    assert row.status == "failed"
    assert row.error == "config file not found"


def test_job_store_list_jobs_returns_most_recent_first_with_total_count(
    conn: sqlite3.Connection, instance_id: str
):
    store = JobStore(conn)
    store.create_job(
        "job1", instance_id, "obsidian", "/vault", ["neo4j"], "default.yml", "2026-07-20T00:00:00"
    )
    store.create_job(
        "job2", instance_id, "obsidian", "/vault", ["neo4j"], "default.yml", "2026-07-21T00:00:00"
    )
    store.create_job(
        "job3", instance_id, "obsidian", "/vault", ["neo4j"], "default.yml", "2026-07-19T00:00:00"
    )

    rows, total = store.list_jobs(limit=20, offset=0)

    assert total == 3
    assert [row.id for row in rows] == ["job2", "job1", "job3"]


def test_job_store_list_jobs_paginates_with_limit_and_offset(
    conn: sqlite3.Connection, instance_id: str
):
    store = JobStore(conn)
    for i in range(5):
        store.create_job(
            f"job{i}",
            instance_id,
            "obsidian",
            "/vault",
            ["neo4j"],
            "default.yml",
            f"2026-07-2{i}T00:00:00",
        )

    page1, total = store.list_jobs(limit=2, offset=0)
    page2, _ = store.list_jobs(limit=2, offset=2)

    assert total == 5
    assert [row.id for row in page1] == ["job4", "job3"]
    assert [row.id for row in page2] == ["job2", "job1"]


def test_job_store_list_jobs_filters_by_instance_id(conn: sqlite3.Connection):
    store = JobStore(conn)
    instances = InstanceStore(conn)
    instances.create_instance("inst-a", "A", "obsidian", "/vault-a", ["neo4j"], "t0")
    instances.create_instance("inst-b", "B", "docx", "/vault-b", ["neo4j"], "t0")
    store.create_job("job1", "inst-a", "obsidian", "/vault-a", ["neo4j"], "a.yml", "t1")
    store.create_job("job2", "inst-b", "docx", "/vault-b", ["neo4j"], "b.yml", "t2")
    store.create_job("job3", "inst-a", "obsidian", "/vault-a", ["neo4j"], "a.yml", "t3")

    rows, total = store.list_jobs(instance_id="inst-a")

    assert total == 2
    assert {row.id for row in rows} == {"job1", "job3"}


def test_job_store_two_jobs_do_not_corrupt_each_others_rows(conn: sqlite3.Connection):
    store = JobStore(conn)
    hash_store = HashStore(conn)
    instances = InstanceStore(conn)
    instances.create_instance("inst-a", "A", "obsidian", "/vault-a", ["neo4j"], "t0")
    instances.create_instance("inst-b", "B", "docx", "/vault-b", ["neo4j"], "t0")

    store.create_job("job1", "inst-a", "obsidian", "/vault-a", ["neo4j"], "a.yml", "t0")
    hash_store.set_hash("obsidian", "a.md", "hash-a", "t0")
    store.complete_job(
        "job1", "completed", JobResult(doc_statuses=[DocStatus("a.md", "updated")]), "t1"
    )

    store.create_job("job2", "inst-b", "docx", "/vault-b", ["neo4j"], "b.yml", "t2")
    hash_store.set_hash("docx", "b.docx", "hash-b", "t2")
    store.complete_job(
        "job2", "completed", JobResult(doc_statuses=[DocStatus("b.docx", "updated")]), "t3"
    )

    job1 = store.get_job("job1")
    job2 = store.get_job("job2")
    assert job1 is not None and job2 is not None
    assert [s.doc_id for s in job1.result.doc_statuses] == ["a.md"]  # type: ignore[union-attr]
    assert [s.doc_id for s in job2.result.doc_statuses] == ["b.docx"]  # type: ignore[union-attr]
    assert hash_store.get_hash("obsidian", "a.md") == "hash-a"
    assert hash_store.get_hash("docx", "b.docx") == "hash-b"
