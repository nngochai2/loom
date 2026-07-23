"""The Jobs API router (spec §8): the FastAPI surface over `JobRunner`.

    POST   /jobs              {instance_id, config_id} -> {job_id}
    GET    /jobs               -> paginated job history; ?instance_id= filters to one instance
    GET    /jobs/{id}          -> status, progress %, per-doc results
    POST   /jobs/{id}/cancel

Polling only — no SSE (spec §9: it has caused problems in the team's
corporate proxy environment).

`POST /jobs` takes `{instance_id, config_id}` (ADR-0025) rather than raw
source/sink fields — `source_type`/`source_path`/`sinks` are resolved from
the instance here, then forwarded to `JobRunner.start`, so `JobRunner`
itself stays about run mechanics rather than instance bookkeeping.

`create_jobs_router` takes a `JobRunner` and `InstanceStore` rather than
reaching for global state, so tests can wire a fake source/sink registry
through it exactly like `cli.run_ingest` does.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.jobs.runner import JobRunner
from app.jobs.store import InstanceStore, JobRow


class CreateJobRequest(BaseModel):
    instance_id: str
    config_id: str


class CreateJobResponse(BaseModel):
    job_id: str


class DocStatusOut(BaseModel):
    doc_id: str
    outcome: str
    error: str | None = None
    warning: str | None = None


class OrphanFlagOut(BaseModel):
    edge_id: str
    reason: str


class JobOut(BaseModel):
    id: str
    instance_id: str
    source_type: str
    source_path: str
    sinks: list[str]
    config_id: str
    status: str
    progress: float
    doc_statuses: list[DocStatusOut]
    orphans: list[OrphanFlagOut]
    error: str | None
    created_at: str
    updated_at: str


class JobListResponse(BaseModel):
    jobs: list[JobOut]
    total: int
    limit: int
    offset: int


def _to_job_out(row: JobRow) -> JobOut:
    doc_statuses = row.result.doc_statuses if row.result is not None else []
    orphans = row.result.orphans if row.result is not None else []
    return JobOut(
        id=row.id,
        instance_id=row.instance_id,
        source_type=row.source_type,
        source_path=row.source_path,
        sinks=row.sinks,
        config_id=row.config_id,
        status=row.status,
        progress=row.progress,
        doc_statuses=[
            DocStatusOut(doc_id=s.doc_id, outcome=s.outcome, error=s.error, warning=s.warning)
            for s in doc_statuses
        ],
        orphans=[OrphanFlagOut(edge_id=o.edge_id, reason=o.reason) for o in orphans],
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def create_jobs_router(runner: JobRunner, instances: InstanceStore) -> APIRouter:
    router = APIRouter(prefix="/jobs", tags=["jobs"])

    @router.post("", response_model=CreateJobResponse, status_code=201)
    async def create_job(payload: CreateJobRequest) -> CreateJobResponse:
        instance = instances.get_instance(payload.instance_id)
        if instance is None:
            raise HTTPException(404, f"Instance not found: {payload.instance_id!r}")
        if instance.source_type not in runner.sources:
            raise HTTPException(422, f"Unknown source_type: {instance.source_type!r}")
        unknown_sinks = [s for s in instance.sinks if s not in runner.sinks]
        if unknown_sinks:
            raise HTTPException(422, f"Unknown sink(s): {', '.join(unknown_sinks)}")

        job_id = await runner.start(
            instance_id=instance.id,
            source_type=instance.source_type,
            source_path=instance.source_path,
            sink_types=instance.sinks,
            config_id=payload.config_id,
        )
        return CreateJobResponse(job_id=job_id)

    @router.get("", response_model=JobListResponse)
    async def list_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        instance_id: str | None = Query(default=None),
    ) -> JobListResponse:
        rows, total = runner.store.list_jobs(limit=limit, offset=offset, instance_id=instance_id)
        return JobListResponse(
            jobs=[_to_job_out(row) for row in rows], total=total, limit=limit, offset=offset
        )

    @router.get("/{job_id}", response_model=JobOut)
    async def get_job(job_id: str) -> JobOut:
        row = runner.store.get_job(job_id)
        if row is None:
            raise HTTPException(404, "Job not found")
        return _to_job_out(row)

    @router.post("/{job_id}/cancel", response_model=JobOut)
    async def cancel_job(job_id: str) -> JobOut:
        row = runner.store.get_job(job_id)
        if row is None:
            raise HTTPException(404, "Job not found")
        if row.status not in ("pending", "running"):
            raise HTTPException(409, f"Job is already {row.status}")

        runner.cancel(job_id)
        updated = runner.store.get_job(job_id)
        assert updated is not None  # the row we just read can't vanish under us
        return _to_job_out(updated)

    return router
