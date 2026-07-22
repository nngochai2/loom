"""JobRunner (spec §8): dispatches Pipeline.run in the background and wires
its ProgressCallback into JobStore. Fake source/sink registries are
injected exactly like `cli.run_ingest` does — no live Neo4j needed.
"""

from __future__ import annotations

import asyncio

from app.jobs.runner import JobRunner
from app.jobs.store import JobRow, connect
from app.pipeline.types import ExtractionVersion
from fakes_jobs import ControllableSource, RecordingSink, ScriptedSource, doc


def _make_runner(source: object, sink: object) -> JobRunner:
    conn = connect(":memory:")
    return JobRunner(
        conn,
        sources={"fake": (lambda config: source, lambda path: None)},  # type: ignore[dict-item]
        sinks={"dryrun": lambda: sink},  # type: ignore[dict-item]
    )


async def _wait_for_terminal(runner: JobRunner, job_id: str, timeout: float = 5.0) -> JobRow:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        row = runner.store.get_job(job_id)
        assert row is not None
        if row.status in ("completed", "failed", "cancelled"):
            return row
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"job {job_id} never reached a terminal status (stuck at {row.status})")
        await asyncio.sleep(0.02)


async def test_start_returns_a_job_id_immediately_and_runs_to_completion():
    source = ScriptedSource([doc("a"), doc("b")])
    sink = RecordingSink()
    runner = _make_runner(source, sink)

    job_id = await runner.start(
        source_type="fake", source_path="/vault", sink_types=["dryrun"], config_id="cfg.yml"
    )
    row = await _wait_for_terminal(runner, job_id)

    assert row.status == "completed"
    assert row.progress == 1.0
    assert row.result is not None
    assert [s.outcome for s in row.result.doc_statuses] == ["updated", "updated"]
    assert [doc_id for doc_id, _ in sink.writes] == ["a", "b"]


async def test_progress_is_recorded_monotonically_during_the_run():
    source = ScriptedSource([doc("a"), doc("b"), doc("c")])
    sink = RecordingSink()
    runner = _make_runner(source, sink)

    job_id = await runner.start(
        source_type="fake", source_path="/vault", sink_types=["dryrun"], config_id="cfg.yml"
    )
    seen: list[float] = []
    while True:
        row = runner.store.get_job(job_id)
        assert row is not None
        seen.append(row.progress)
        if row.status == "completed":
            break
        await asyncio.sleep(0.01)

    assert seen == sorted(seen)  # monotonically non-decreasing
    assert seen[-1] == 1.0


async def test_cancel_stops_the_job_before_unstarted_docs_are_processed():
    source = ControllableSource([doc("a"), doc("b"), doc("c")])
    sink = RecordingSink()
    runner = _make_runner(source, sink)

    job_id = await runner.start(
        source_type="fake", source_path="/vault", sink_types=["dryrun"], config_id="cfg.yml"
    )
    await asyncio.to_thread(source.started.wait, 5)  # doc "a" load() has begun

    assert runner.cancel(job_id) is True
    source.release.set()  # let doc "a" finish; runner checks should_cancel before "b"

    row = await _wait_for_terminal(runner, job_id)

    assert row.status == "cancelled"
    assert source.loaded == ["a"]  # "b" and "c" never started
    assert [doc_id for doc_id, _ in sink.writes] == ["a"]  # "a" not rolled back
    assert row.result is not None
    assert [s.doc_id for s in row.result.doc_statuses] == ["a"]


async def test_cancel_on_an_unknown_job_id_returns_false():
    runner = _make_runner(ScriptedSource([]), RecordingSink())

    assert runner.cancel("never-started") is False


async def test_cancel_after_completion_returns_false():
    source = ScriptedSource([doc("a")])
    sink = RecordingSink()
    runner = _make_runner(source, sink)

    job_id = await runner.start(
        source_type="fake", source_path="/vault", sink_types=["dryrun"], config_id="cfg.yml"
    )
    await _wait_for_terminal(runner, job_id)

    assert runner.cancel(job_id) is False


async def test_unknown_source_type_fails_the_job_instead_of_raising():
    runner = _make_runner(ScriptedSource([]), RecordingSink())

    job_id = await runner.start(
        source_type="does-not-exist", source_path="/vault", sink_types=["dryrun"], config_id="cfg.yml"
    )
    row = await _wait_for_terminal(runner, job_id)

    assert row.status == "failed"
    assert row.error is not None


async def test_runner_reuses_persistent_hash_store_across_jobs_for_incremental_reingestion():
    source = ScriptedSource([doc("a")])
    sink = RecordingSink()
    runner = _make_runner(source, sink)

    job1 = await runner.start(
        source_type="fake", source_path="/vault", sink_types=["dryrun"], config_id="cfg.yml"
    )
    await _wait_for_terminal(runner, job1)
    assert len(sink.writes) == 1

    job2 = await runner.start(
        source_type="fake", source_path="/vault", sink_types=["dryrun"], config_id="cfg.yml"
    )
    row2 = await _wait_for_terminal(runner, job2)

    assert row2.result is not None
    assert [s.outcome for s in row2.result.doc_statuses] == ["skipped"]
    assert len(sink.writes) == 1  # unchanged: the second job wrote nothing new


async def test_unregistered_source_type_gets_no_extraction_version_instead_of_failing():
    # `extraction_version.get(source_type, ...)`, not `[source_type]`: a
    # source_type the injected extraction_version dict doesn't know about
    # (every test fake, by construction) must not turn into a job failure.
    source = ScriptedSource([doc("a")])
    sink = RecordingSink()
    runner = _make_runner(source, sink)  # default extraction_version=EXTRACTION_VERSION

    job_id = await runner.start(
        source_type="fake", source_path="/vault", sink_types=["dryrun"], config_id="cfg.yml"
    )
    row = await _wait_for_terminal(runner, job_id)

    assert row.status == "completed"
    assert [s.outcome for s in row.result.doc_statuses] == ["updated"]


async def test_runner_forwards_extraction_version_and_reruns_on_a_model_change():
    source = ScriptedSource([doc("a")])
    sink = RecordingSink()
    conn = connect(":memory:")
    current_model = {"value": "llama3.1"}
    runner = JobRunner(
        conn,
        sources={"fake": (lambda config: source, lambda path: None)},  # type: ignore[dict-item]
        sinks={"dryrun": lambda: sink},  # type: ignore[dict-item]
        extraction_version={  # type: ignore[dict-item]
            "fake": lambda config: ExtractionVersion(
                prompt_version="1", model=current_model["value"]
            )
        },
    )

    job1 = await runner.start(
        source_type="fake", source_path="/vault", sink_types=["dryrun"], config_id="cfg.yml"
    )
    await _wait_for_terminal(runner, job1)
    assert len(sink.writes) == 1

    job2 = await runner.start(
        source_type="fake", source_path="/vault", sink_types=["dryrun"], config_id="cfg.yml"
    )
    row2 = await _wait_for_terminal(runner, job2)
    assert row2.result is not None
    assert [s.outcome for s in row2.result.doc_statuses] == ["skipped"]
    assert len(sink.writes) == 1  # still just the one write from job1

    current_model["value"] = "llama3.2"
    job3 = await runner.start(
        source_type="fake", source_path="/vault", sink_types=["dryrun"], config_id="cfg.yml"
    )
    row3 = await _wait_for_terminal(runner, job3)
    assert row3.result is not None
    assert [s.outcome for s in row3.result.doc_statuses] == ["updated"]
    assert len(sink.writes) == 2  # reprocessed despite unchanged content_hash


async def test_two_jobs_running_concurrently_do_not_corrupt_each_others_rows():
    # Unlike the back-to-back tests, these two jobs are genuinely in flight
    # at the same time (both blocked mid-doc-load, released together) —
    # exercises the concurrency `store.py`'s _WRITE_LOCK is actually for,
    # not just sequential runs against a shared connection.
    source_a = ControllableSource([doc("a1"), doc("a2")])
    source_b = ControllableSource([doc("b1"), doc("b2")])
    sink = RecordingSink()
    conn = connect(":memory:")
    runner = JobRunner(
        conn,
        sources={
            "fake-a": (lambda config: source_a, lambda path: None),  # type: ignore[dict-item]
            "fake-b": (lambda config: source_b, lambda path: None),  # type: ignore[dict-item]
        },
        sinks={"dryrun": lambda: sink},  # type: ignore[dict-item]
    )
    source_a.source_type = "fake-a"
    source_b.source_type = "fake-b"

    job_a = await runner.start(
        source_type="fake-a", source_path="/vault-a", sink_types=["dryrun"], config_id="a.yml"
    )
    job_b = await runner.start(
        source_type="fake-b", source_path="/vault-b", sink_types=["dryrun"], config_id="b.yml"
    )

    await asyncio.to_thread(source_a.started.wait, 5)
    await asyncio.to_thread(source_b.started.wait, 5)
    source_a.release.set()
    source_b.release.set()

    row_a = await _wait_for_terminal(runner, job_a)
    row_b = await _wait_for_terminal(runner, job_b)

    assert row_a.status == "completed"
    assert row_b.status == "completed"
    assert row_a.result is not None and row_b.result is not None
    assert [s.doc_id for s in row_a.result.doc_statuses] == ["a1", "a2"]
    assert [s.doc_id for s in row_b.result.doc_statuses] == ["b1", "b2"]
    assert {doc_id for doc_id, _ in sink.writes} == {"a1", "a2", "b1", "b2"}
