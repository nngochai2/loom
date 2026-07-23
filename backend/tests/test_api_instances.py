"""The Instances API (ADR-0025/0026): a catalog of source+sink recipes,
never a partition of the graph. Exercised through `app.main.create_app`
with fake source/sink registries, same seam `test_api_jobs.py` uses.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

from app.main import create_app
from fakes_jobs import RecordingSink, ScriptedSource, doc


@contextmanager
def _client(sources: dict | None = None, sinks: dict | None = None) -> Iterator[TestClient]:
    app = create_app(
        db_path=":memory:",
        sources=sources or {"fake": (lambda config: ScriptedSource([]), lambda path: None)},
        sinks=sinks or {"dryrun": lambda: RecordingSink()},
    )
    with TestClient(app) as client:
        yield client


def _poll_until_terminal(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] in ("completed", "failed", "cancelled"):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"job {job_id} never reached a terminal status: {body}")
        time.sleep(0.02)


def test_post_instances_creates_and_returns_id():
    with _client() as client:
        resp = client.post(
            "/instances",
            json={"name": "Q3 Vendor Docs", "source_type": "fake", "source_path": "/data/q3", "sinks": ["dryrun"]},
        )

    assert resp.status_code == 201
    assert "instance_id" in resp.json()


def test_post_instances_auto_generates_name_from_path_when_omitted():
    with _client(
        sources={"docx": (lambda config: ScriptedSource([]), lambda path: None)}
    ) as client:
        resp = client.post(
            "/instances",
            json={"source_type": "docx", "source_path": "/data/inbox/q3-vendor-docs", "sinks": ["dryrun"]},
        )
        instance_id = resp.json()["instance_id"]
        instance = client.get(f"/instances/{instance_id}").json()

    assert instance["name"] == "Documents folder — q3-vendor-docs"


def test_post_instances_rejects_duplicate_source_type_path_sinks():
    with _client() as client:
        client.post(
            "/instances",
            json={"source_type": "fake", "source_path": "/data/q3", "sinks": ["dryrun"]},
        )
        resp = client.post(
            "/instances",
            json={"source_type": "fake", "source_path": "/data/q3", "sinks": ["dryrun"]},
        )

    assert resp.status_code == 409


def test_post_instances_rejects_unknown_source_type():
    with _client() as client:
        resp = client.post(
            "/instances",
            json={"source_type": "bogus", "source_path": ".", "sinks": ["dryrun"]},
        )

    assert resp.status_code == 422


def test_post_instances_rejects_unknown_sink():
    with _client() as client:
        resp = client.post(
            "/instances",
            json={"source_type": "fake", "source_path": ".", "sinks": ["bogus"]},
        )

    assert resp.status_code == 422


def test_get_instances_lists_created_instances():
    with _client() as client:
        client.post(
            "/instances",
            json={"name": "A", "source_type": "fake", "source_path": "/a", "sinks": ["dryrun"]},
        )
        client.post(
            "/instances",
            json={"name": "B", "source_type": "fake", "source_path": "/b", "sinks": ["dryrun"]},
        )

        resp = client.get("/instances")

    names = {i["name"] for i in resp.json()["instances"]}
    assert names == {"A", "B"}


def test_get_instance_returns_404_for_unknown_id():
    with _client() as client:
        resp = client.get("/instances/does-not-exist")

    assert resp.status_code == 404


def test_get_instance_reports_zero_jobs_for_a_freshly_created_instance():
    with _client() as client:
        instance_id = client.post(
            "/instances",
            json={"name": "A", "source_type": "fake", "source_path": "/a", "sinks": ["dryrun"]},
        ).json()["instance_id"]

        resp = client.get(f"/instances/{instance_id}")

    body = resp.json()
    assert body["job_count"] == 0
    assert body["last_status"] is None
    assert body["last_run_at"] is None


def test_patch_instance_renames():
    with _client() as client:
        instance_id = client.post(
            "/instances",
            json={"name": "Old name", "source_type": "fake", "source_path": "/a", "sinks": ["dryrun"]},
        ).json()["instance_id"]

        resp = client.patch(f"/instances/{instance_id}", json={"name": "New name"})

    assert resp.status_code == 200
    assert resp.json()["name"] == "New name"


def test_patch_instance_returns_404_for_unknown_id():
    with _client() as client:
        resp = client.patch("/instances/does-not-exist", json={"name": "New name"})

    assert resp.status_code == 404


def test_delete_instance_removes_it():
    with _client() as client:
        instance_id = client.post(
            "/instances",
            json={"name": "Gone soon", "source_type": "fake", "source_path": "/a", "sinks": ["dryrun"]},
        ).json()["instance_id"]

        delete_resp = client.delete(f"/instances/{instance_id}")
        get_resp = client.get(f"/instances/{instance_id}")

    assert delete_resp.status_code == 204
    assert get_resp.status_code == 404


def test_delete_instance_returns_404_for_unknown_id():
    with _client() as client:
        resp = client.delete("/instances/does-not-exist")

    assert resp.status_code == 404


def test_delete_instance_removes_job_history_but_never_touches_the_sink():
    # ADR-0025: deleting an instance is catalog-only. The sink's own
    # delete_non_curated_for_doc must never be called as a side effect.
    source = ScriptedSource([doc("a")])
    sink = RecordingSink()

    with _client(
        sources={"fake": (lambda config: source, lambda path: None)},
        sinks={"dryrun": lambda: sink},
    ) as client:
        instance_id = client.post(
            "/instances",
            json={"source_type": "fake", "source_path": "/vault", "sinks": ["dryrun"]},
        ).json()["instance_id"]
        job_id = client.post(
            "/jobs", json={"instance_id": instance_id, "config_id": "cfg.yml"}
        ).json()["job_id"]
        _poll_until_terminal(client, job_id)
        assert len(sink.writes) == 1

        client.delete(f"/instances/{instance_id}")

        jobs_after = client.get("/jobs", params={"instance_id": instance_id}).json()

    assert jobs_after["total"] == 0
    assert sink.deletes == []  # catalog-only: no cascade into the sink
