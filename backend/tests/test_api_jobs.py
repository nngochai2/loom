"""The Jobs API (spec §8, §10 Phase 2 gate): `POST /jobs` observable through
completion by polling `GET /jobs/{id}`, `POST /jobs/{id}/cancel` stopping a
run mid-flight, and `GET /jobs` paginating history. Exercised through
`app.main.create_app` with fake source/sink registries injected (same seam
`cli.run_ingest`/`JobRunner` leave at their defaults) — no live Neo4j, no
TestClient timing races (a `ControllableSource` blocks on a `threading.Event`
so the test controls exactly when a job is "mid-run").

Every test uses `with _client(...) as client:` — plain `TestClient(app)`
without the context manager opens a fresh event loop per request and tears
it down when that request returns, orphaning the `asyncio.create_task`
`JobRunner.start` schedules; entering the context manager keeps one portal
(and its event loop) alive for the background job to actually finish on.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline.types import SourceDoc
from fakes_jobs import ControllableSource, RecordingSink, ScriptedSource, doc


class _NamedSource(ScriptedSource):
    """A distinct `source_type` per instance, so two unrelated jobs in the
    same test don't share HashStore rows just because both use
    `ScriptedSource`'s default 'fake' type — matches how real adapters
    each declare their own `source_type` (spec §6.1 keys hashes by it)."""

    def __init__(self, source_type: str, docs: list[SourceDoc]) -> None:
        super().__init__(docs)
        self.source_type = source_type


@contextmanager
def _client(
    sources: dict[str, tuple[object, object]], sinks: dict[str, object]
) -> Iterator[TestClient]:
    app = create_app(db_path=":memory:", sources=sources, sinks=sinks)  # type: ignore[arg-type]
    with TestClient(app) as client:
        yield client


@contextmanager
def _single_fake_client(source: object, sink: object) -> Iterator[TestClient]:
    with _client(
        sources={"fake": (lambda config: source, lambda path: None)},
        sinks={"dryrun": lambda: sink},
    ) as client:
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


def test_post_jobs_returns_a_job_id_observable_through_completion_by_polling():
    source = ScriptedSource([doc("a"), doc("b")])
    sink = RecordingSink()

    with _single_fake_client(source, sink) as client:
        create_resp = client.post(
            "/jobs",
            json={
                "source_type": "fake",
                "source_path": "/vault",
                "sinks": ["dryrun"],
                "config_id": "cfg.yml",
            },
        )
        assert create_resp.status_code == 201
        job_id = create_resp.json()["job_id"]

        body = _poll_until_terminal(client, job_id)

    assert body["status"] == "completed"
    assert body["progress"] == 1.0
    assert [d["outcome"] for d in body["doc_statuses"]] == ["updated", "updated"]
    assert len(sink.writes) == 2


def test_get_job_surfaces_a_degraded_docs_warning_without_marking_it_failed():
    # ADR-0022/issue #20: a doc whose extraction degraded (e.g. prose
    # extraction hit an unreachable Ollama) stays "updated", not "failed" --
    # its warning is exposed alongside the other per-doc fields so a future
    # Ingest UI can render it (the same expandable-detail pattern already
    # planned for orphan warnings).
    source = ScriptedSource([doc("a")], warnings={"a": "prose extraction failed: timed out"})
    sink = RecordingSink()

    with _single_fake_client(source, sink) as client:
        create_resp = client.post(
            "/jobs",
            json={
                "source_type": "fake",
                "source_path": "/vault",
                "sinks": ["dryrun"],
                "config_id": "cfg.yml",
            },
        )
        job_id = create_resp.json()["job_id"]

        body = _poll_until_terminal(client, job_id)

    assert len(body["doc_statuses"]) == 1
    status = body["doc_statuses"][0]
    assert status["outcome"] == "updated"
    assert status["warning"] == "prose extraction failed: timed out"
    assert status["error"] is None


def test_post_jobs_rejects_unknown_source_type():
    with _single_fake_client(ScriptedSource([]), RecordingSink()) as client:
        resp = client.post(
            "/jobs",
            json={"source_type": "bogus", "source_path": ".", "sinks": ["dryrun"], "config_id": "x"},
        )

    assert resp.status_code == 422


def test_post_jobs_rejects_unknown_sink():
    with _single_fake_client(ScriptedSource([]), RecordingSink()) as client:
        resp = client.post(
            "/jobs",
            json={"source_type": "fake", "source_path": ".", "sinks": ["bogus"], "config_id": "x"},
        )

    assert resp.status_code == 422


def test_get_job_returns_404_for_unknown_id():
    with _single_fake_client(ScriptedSource([]), RecordingSink()) as client:
        resp = client.get("/jobs/does-not-exist")

    assert resp.status_code == 404


def test_cancel_returns_404_for_unknown_id():
    with _single_fake_client(ScriptedSource([]), RecordingSink()) as client:
        resp = client.post("/jobs/does-not-exist/cancel")

    assert resp.status_code == 404


def test_cancel_stops_a_running_job_and_reflects_cancellation_when_polled():
    source = ControllableSource([doc("a"), doc("b"), doc("c")])
    sink = RecordingSink()

    with _single_fake_client(source, sink) as client:
        job_id = client.post(
            "/jobs",
            json={
                "source_type": "fake",
                "source_path": "/vault",
                "sinks": ["dryrun"],
                "config_id": "cfg.yml",
            },
        ).json()["job_id"]

        assert source.started.wait(5)  # doc "a" load() has begun
        cancel_resp = client.post(f"/jobs/{job_id}/cancel")
        assert cancel_resp.status_code == 200
        source.release.set()  # let doc "a" finish

        body = _poll_until_terminal(client, job_id)

    assert body["status"] == "cancelled"
    assert source.loaded == ["a"]
    assert len(sink.writes) == 1  # "a" written, not rolled back


def test_cancel_on_an_already_completed_job_returns_409():
    source = ScriptedSource([doc("a")])

    with _single_fake_client(source, RecordingSink()) as client:
        job_id = client.post(
            "/jobs",
            json={
                "source_type": "fake",
                "source_path": "/vault",
                "sinks": ["dryrun"],
                "config_id": "cfg.yml",
            },
        ).json()["job_id"]
        _poll_until_terminal(client, job_id)

        resp = client.post(f"/jobs/{job_id}/cancel")

    assert resp.status_code == 409


def test_get_jobs_paginates_history_across_multiple_runs():
    source = ScriptedSource([])

    with _single_fake_client(source, RecordingSink()) as client:
        job_ids = []
        for _ in range(3):
            resp = client.post(
                "/jobs",
                json={
                    "source_type": "fake",
                    "source_path": "/vault",
                    "sinks": ["dryrun"],
                    "config_id": "cfg.yml",
                },
            )
            job_ids.append(resp.json()["job_id"])
            _poll_until_terminal(client, job_ids[-1])

        page1 = client.get("/jobs", params={"limit": 2, "offset": 0}).json()
        page2 = client.get("/jobs", params={"limit": 2, "offset": 2}).json()

    assert page1["total"] == 3
    assert len(page1["jobs"]) == 2
    assert len(page2["jobs"]) == 1
    seen_ids = {j["id"] for j in page1["jobs"]} | {j["id"] for j in page2["jobs"]}
    assert seen_ids == set(job_ids)


def test_two_jobs_run_back_to_back_do_not_corrupt_each_others_rows():
    sink = RecordingSink()
    sources = {
        "fake-a": (lambda config: _NamedSource("fake-a", [doc("a")]), lambda path: None),
        "fake-b": (lambda config: _NamedSource("fake-b", [doc("b")]), lambda path: None),
    }

    with _client(sources=sources, sinks={"dryrun": lambda: sink}) as client:
        job1 = client.post(
            "/jobs",
            json={
                "source_type": "fake-a",
                "source_path": "/vault-a",
                "sinks": ["dryrun"],
                "config_id": "a.yml",
            },
        ).json()["job_id"]
        body1 = _poll_until_terminal(client, job1)

        job2 = client.post(
            "/jobs",
            json={
                "source_type": "fake-b",
                "source_path": "/vault-b",
                "sinks": ["dryrun"],
                "config_id": "b.yml",
            },
        ).json()["job_id"]
        body2 = _poll_until_terminal(client, job2)

    assert [d["doc_id"] for d in body1["doc_statuses"]] == ["a"]
    assert [d["doc_id"] for d in body2["doc_statuses"]] == ["b"]
    assert {doc_id for doc_id, _ in sink.writes} == {"a", "b"}
