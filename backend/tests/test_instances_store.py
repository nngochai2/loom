import sqlite3

import pytest

from app.jobs.store import DuplicateInstanceError, InstanceStore, JobStore, connect
from app.pipeline.types import JobResult


@pytest.fixture()
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


def test_create_instance_then_get_round_trips(conn: sqlite3.Connection):
    store = InstanceStore(conn)

    store.create_instance("i1", "Q3 Vendor Docs", "docx", "/data/q3", ["neo4j"], "t0")
    row = store.get_instance("i1")

    assert row is not None
    assert row.name == "Q3 Vendor Docs"
    assert row.source_type == "docx"
    assert row.source_path == "/data/q3"
    assert row.sinks == ["neo4j"]
    assert row.job_count == 0
    assert row.last_status is None
    assert row.last_run_at is None


def test_get_instance_returns_none_for_unknown_id(conn: sqlite3.Connection):
    store = InstanceStore(conn)

    assert store.get_instance("never-created") is None


def test_create_instance_rejects_duplicate_source_type_path_sinks(conn: sqlite3.Connection):
    store = InstanceStore(conn)
    store.create_instance("i1", "First", "docx", "/data/q3", ["neo4j"], "t0")

    with pytest.raises(DuplicateInstanceError):
        store.create_instance("i2", "Second", "docx", "/data/q3", ["neo4j"], "t1")


def test_create_instance_treats_sink_order_as_equivalent_for_duplicate_detection(
    conn: sqlite3.Connection,
):
    # (ADR-0025) identity is the *set* of sinks, not the order they were given in.
    store = InstanceStore(conn)
    store.create_instance("i1", "First", "docx", "/data/q3", ["neo4j", "chroma"], "t0")

    with pytest.raises(DuplicateInstanceError):
        store.create_instance("i2", "Second", "docx", "/data/q3", ["chroma", "neo4j"], "t1")


def test_create_instance_allows_same_path_with_a_different_sink(conn: sqlite3.Connection):
    store = InstanceStore(conn)
    store.create_instance("i1", "Graph", "docx", "/data/q3", ["neo4j"], "t0")

    store.create_instance("i2", "Vector", "docx", "/data/q3", ["chroma"], "t1")  # must not raise

    assert store.get_instance("i2") is not None


def test_rename_instance_updates_name_and_updated_at(conn: sqlite3.Connection):
    store = InstanceStore(conn)
    store.create_instance("i1", "Old name", "docx", "/data/q3", ["neo4j"], "t0")

    store.rename_instance("i1", "New name", "t1")

    row = store.get_instance("i1")
    assert row is not None
    assert row.name == "New name"
    assert row.updated_at == "t1"


def test_delete_instance_removes_the_row(conn: sqlite3.Connection):
    store = InstanceStore(conn)
    store.create_instance("i1", "Gone soon", "docx", "/data/q3", ["neo4j"], "t0")

    store.delete_instance("i1")

    assert store.get_instance("i1") is None


def test_delete_instance_also_removes_its_job_history(conn: sqlite3.Connection):
    instances = InstanceStore(conn)
    jobs = JobStore(conn)
    instances.create_instance("i1", "Gone soon", "docx", "/data/q3", ["neo4j"], "t0")
    jobs.create_job("job1", "i1", "docx", "/data/q3", ["neo4j"], "cfg.yml", "t0")

    instances.delete_instance("i1")

    assert jobs.get_job("job1") is None


def test_list_instances_orders_by_most_recent_job_first(conn: sqlite3.Connection):
    instances = InstanceStore(conn)
    jobs = JobStore(conn)
    instances.create_instance("i1", "A", "docx", "/a", ["neo4j"], "2026-07-01T00:00:00")
    instances.create_instance("i2", "B", "docx", "/b", ["neo4j"], "2026-07-02T00:00:00")
    # i2 was created after i1, but i1's most recent job is more recent than i2's.
    jobs.create_job("job-b1", "i2", "docx", "/b", ["neo4j"], "cfg.yml", "2026-07-03T00:00:00")
    jobs.create_job("job-a1", "i1", "docx", "/a", ["neo4j"], "cfg.yml", "2026-07-10T00:00:00")

    rows = instances.list_instances()

    assert [row.id for row in rows] == ["i1", "i2"]


def test_list_instances_with_no_jobs_sorts_by_created_at(conn: sqlite3.Connection):
    instances = InstanceStore(conn)
    instances.create_instance("i1", "A", "docx", "/a", ["neo4j"], "2026-07-01T00:00:00")
    instances.create_instance("i2", "B", "docx", "/b", ["neo4j"], "2026-07-05T00:00:00")

    rows = instances.list_instances()

    assert [row.id for row in rows] == ["i2", "i1"]


def test_list_instances_reports_job_count_and_last_status(conn: sqlite3.Connection):
    instances = InstanceStore(conn)
    jobs = JobStore(conn)
    instances.create_instance("i1", "A", "docx", "/a", ["neo4j"], "t0")
    jobs.create_job("job1", "i1", "docx", "/a", ["neo4j"], "cfg.yml", "2026-07-01T00:00:00")
    jobs.create_job("job2", "i1", "docx", "/a", ["neo4j"], "cfg.yml", "2026-07-02T00:00:00")
    jobs.mark_running("job2", "2026-07-02T00:00:01")
    jobs.complete_job("job2", "completed", JobResult(), "2026-07-02T00:00:02")

    row = instances.get_instance("i1")

    assert row is not None
    assert row.job_count == 2
    assert row.last_status == "completed"
    assert row.last_run_at == "2026-07-02T00:00:00"
