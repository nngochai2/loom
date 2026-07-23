"""The Instances API router (spec §8, ADR-0025/0026): a catalog of
source+sink recipes, never a partition of the graph itself.

    POST   /instances          {name?, source_type, source_path, sinks[]} -> {instance_id}
    GET    /instances          -> list, most-recently-run first
    GET    /instances/{id}     -> source_type, source_path, sinks, latest job summary
    PATCH  /instances/{id}     {name} -> rename
    DELETE /instances/{id}     -> catalog-only; underlying graph/vector data untouched

`create_instances_router` takes a `JobRunner`, the same seam
`create_jobs_router` uses — `runner.sources`/`runner.sinks` validate an
instance's source_type/sinks at creation time (rather than only when a job
is later run against it), and `runner.instances` (ADR-0025) is the store.
"""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api._registry_validation import validate_source_and_sinks
from app.jobs.runner import JobRunner
from app.jobs.store import DuplicateInstanceError, InstanceRow, now_iso

_SOURCE_LABELS = {"obsidian": "Obsidian vault", "docx": "Documents folder"}


def _auto_name(source_type: str, source_path: str) -> str:
    label = _SOURCE_LABELS.get(source_type, source_type)
    base = os.path.basename(source_path.rstrip("/\\")) or source_path
    return f"{label} — {base}"


class CreateInstanceRequest(BaseModel):
    name: str | None = None
    source_type: str
    source_path: str
    sinks: list[str]


class CreateInstanceResponse(BaseModel):
    instance_id: str


class InstanceOut(BaseModel):
    id: str
    name: str
    source_type: str
    source_path: str
    sinks: list[str]
    created_at: str
    updated_at: str
    job_count: int
    last_status: str | None
    last_run_at: str | None


class InstanceListResponse(BaseModel):
    instances: list[InstanceOut]


class RenameInstanceRequest(BaseModel):
    name: str


def _to_instance_out(row: InstanceRow) -> InstanceOut:
    return InstanceOut(
        id=row.id,
        name=row.name,
        source_type=row.source_type,
        source_path=row.source_path,
        sinks=row.sinks,
        created_at=row.created_at,
        updated_at=row.updated_at,
        job_count=row.job_count,
        last_status=row.last_status,
        last_run_at=row.last_run_at,
    )


def create_instances_router(
    runner: JobRunner,
) -> APIRouter:
    instances = runner.instances
    router = APIRouter(prefix="/instances", tags=["instances"])

    @router.post("", response_model=CreateInstanceResponse, status_code=201)
    async def create_instance(payload: CreateInstanceRequest) -> CreateInstanceResponse:
        validate_source_and_sinks(runner.sources, runner.sinks, payload.source_type, payload.sinks)

        # A blank/whitespace-only name is treated the same as an omitted
        # one — nothing downstream (the Instances page, a rename prompt)
        # wants to display an instance with an empty label.
        name = (payload.name or "").strip() or _auto_name(payload.source_type, payload.source_path)
        instance_id = uuid.uuid4().hex
        try:
            instances.create_instance(
                instance_id, name, payload.source_type, payload.source_path, payload.sinks, now_iso()
            )
        except DuplicateInstanceError as exc:
            raise HTTPException(409, str(exc)) from exc
        return CreateInstanceResponse(instance_id=instance_id)

    @router.get("", response_model=InstanceListResponse)
    async def list_instances() -> InstanceListResponse:
        return InstanceListResponse(instances=[_to_instance_out(row) for row in instances.list_instances()])

    @router.get("/{instance_id}", response_model=InstanceOut)
    async def get_instance(instance_id: str) -> InstanceOut:
        row = instances.get_instance(instance_id)
        if row is None:
            raise HTTPException(404, "Instance not found")
        return _to_instance_out(row)

    @router.patch("/{instance_id}", response_model=InstanceOut)
    async def rename_instance(instance_id: str, payload: RenameInstanceRequest) -> InstanceOut:
        if instances.get_instance(instance_id) is None:
            raise HTTPException(404, "Instance not found")
        instances.rename_instance(instance_id, payload.name, now_iso())
        row = instances.get_instance(instance_id)
        assert row is not None  # can't vanish between the rename and this read
        return _to_instance_out(row)

    @router.delete("/{instance_id}", status_code=204)
    async def delete_instance(instance_id: str) -> None:
        if instances.get_instance(instance_id) is None:
            raise HTTPException(404, "Instance not found")
        instances.delete_instance(instance_id)

    return router
