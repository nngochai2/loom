"""The Jobs API's async runner (spec §8): dispatches a job onto Phase 1's
`Pipeline.run` without an external queue — "run in-process via async task"
means literally that. `start()` fires the run as an `asyncio.Task` and
returns the `job_id` immediately; the run itself executes `Pipeline.run`
(a blocking, synchronous method) via `asyncio.to_thread` so it doesn't stall
the event loop for the whole job duration.

Progress and cancellation both go through the same two seams `Pipeline.run`
already exposes: the `progress` callback writes into `JobStore` on every
doc boundary, and `should_cancel` is a zero-arg callable backed by a
per-job `threading.Event` that `POST /jobs/{id}/cancel` sets. `JobStore`'s
own write lock (see `app/jobs/store.py`) is what makes writing from this
worker thread safe.

Registries are injectable (default to the real `SOURCES`/`SINKS`), the same
seam `cli.run_ingest` uses, so tests can substitute fakes without a live
Neo4j.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import uuid
from typing import Any, Callable

from app.jobs.store import HashStore, InstanceStore, JobStatusValue, JobStore, now_iso
from app.pipeline.core import Pipeline
from app.pipeline.registry import EXTRACTION_VERSION, SINKS, SOURCES
from app.pipeline.sinks.base import SinkAdapter
from app.pipeline.sources.base import SourceAdapter
from app.pipeline.types import ExtractionVersion


class JobRunner:
    def __init__(
        self,
        conn: sqlite3.Connection,
        sources: dict[str, tuple[type, Callable[[str], Any]]] = SOURCES,
        sinks: dict[str, Callable[[], SinkAdapter]] = SINKS,
        extraction_version: dict[str, Callable[[Any], ExtractionVersion | None]] = EXTRACTION_VERSION,
    ) -> None:
        self.store = JobStore(conn)
        # Same conn as `self.store`, exposed here so callers (the Jobs API,
        # tests) that already hold a `JobRunner` don't need a second wiring
        # path to reach the instance catalog (ADR-0025).
        self.instances = InstanceStore(conn)
        self.sources = sources
        self.sinks = sinks
        self.extraction_version = extraction_version
        # One HashStore shared across every job's run: it's a stateless
        # CRUD wrapper over `conn` (see `store.py`'s write lock), so there's
        # nothing job-specific to isolate by holding a separate instance.
        self._hash_store = HashStore(conn)
        self._cancel_events: dict[str, threading.Event] = {}
        # asyncio only holds a weak reference to a task once nothing else
        # references it — without this, a run started here could be
        # garbage-collected mid-flight. See the asyncio.create_task docs'
        # own warning about keeping a strong reference.
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(
        self,
        instance_id: str,
        source_type: str,
        source_path: str,
        sink_types: list[str],
        config_id: str,
    ) -> str:
        """Create the job row and fire its run in the background. Returns
        immediately with the new `job_id` — callers observe progress via
        `JobStore`/`GET /jobs/{id}`, not by awaiting this call.

        `instance_id` (ADR-0025) is recorded on the job row but not itself
        resolved here — the Jobs API looks it up via `InstanceStore` first
        and passes its `source_type`/`source_path`/`sink_types` through, so
        this method (and its tests) stay about run mechanics, not instance
        bookkeeping."""
        job_id = uuid.uuid4().hex
        self.store.create_job(
            job_id, instance_id, source_type, source_path, sink_types, config_id, now_iso()
        )

        cancel_event = threading.Event()
        self._cancel_events[job_id] = cancel_event
        task = asyncio.create_task(
            self._run(job_id, source_type, source_path, sink_types, config_id, cancel_event)
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))
        return job_id

    def cancel(self, job_id: str) -> bool:
        """Signal a running job to stop at its next doc boundary (spec §8).
        Returns False if `job_id` isn't currently running (unknown, or
        already terminal) — the caller decides what that means for the
        HTTP response."""
        event = self._cancel_events.get(job_id)
        if event is None:
            return False
        event.set()
        return True

    async def _run(
        self,
        job_id: str,
        source_type: str,
        source_path: str,
        sink_types: list[str],
        config_id: str,
        cancel_event: threading.Event,
    ) -> None:
        self.store.mark_running(job_id, now_iso())
        try:
            adapter_cls, config_loader = self.sources[source_type]
            config = config_loader(config_id)
            source: SourceAdapter = adapter_cls(config)
            active_sinks = [self.sinks[name]() for name in sink_types]
            # `.get(..., lambda config: None)`, not `[source_type]`: an
            # unregistered source_type (test fakes, most obviously) simply
            # has no LLM fingerprint concept rather than failing the job —
            # unlike `self.sources`/`self.sinks` above, where an unknown key
            # legitimately should fail the job.
            extraction_version = self.extraction_version.get(source_type, lambda config: None)(
                config
            )

            def progress(doc_id: str, fraction: float) -> None:
                self.store.record_progress(job_id, fraction, now_iso())

            result = await asyncio.to_thread(
                Pipeline().run,
                source=source,
                source_path=source_path,
                sinks=active_sinks,
                config=config,
                progress=progress,
                store=self._hash_store,
                should_cancel=cancel_event.is_set,
                extraction_version=extraction_version,
            )
            status: JobStatusValue = "cancelled" if cancel_event.is_set() else "completed"
            self.store.complete_job(job_id, status, result, now_iso())
        except Exception as exc:
            # Anything raised outside Pipeline.run's own per-doc try/except
            # (unknown source_type/sink, an unreadable config file, ...) —
            # surfaced as the job's terminal error rather than crashing the
            # background task silently.
            self.store.fail_job(job_id, str(exc), now_iso())
        finally:
            self._cancel_events.pop(job_id, None)
