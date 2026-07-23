"""The Instances API router (spec §8, ADR-0025/0026): a catalog of
source+sink recipes, never a partition of the graph itself.

    POST   /instances          {name?, source_type, source_path, sinks[]} -> {instance_id}
    GET    /instances          -> list, most-recently-run first
    GET    /instances/{id}     -> source_type, source_path, sinks, latest job summary
    PATCH  /instances/{id}     {name} -> rename
    DELETE /instances/{id}     -> catalog-only; underlying graph/vector data untouched

`create_instances_router` takes the same injectable source/sink registries
`create_jobs_router` does, so an instance's source_type/sinks are validated
against the real registries at creation time rather than only when a job
is later run against it.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.jobs.store import DuplicateInstanceError, InstanceRow, InstanceStore

_SOURCE_LABELS = {"obsidian": "Obsidian vault", "docx": "Documents folder"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
    instances: InstanceStore,
    sources: dict[str, Any],
    sinks: dict[str, Any],
) -> APIRouter:
    router = APIRouter(prefix="/instances", tags=["instances"])

    @router.post("", response_model=CreateInstanceResponse, status_code=201)
    async def create_instance(payload: CreateInstanceRequest) -> CreateInstanceResponse:
        if payload.source_type not in sources:
            raise HTTPException(422, f"Unknown source_type: {payload.source_type!r}")
        unknown_sinks = [s for s in payload.sinks if s not in sinks]
        if unknown_sinks:
            raise HTTPException(422, f"Unknown sink(s): {', '.join(unknown_sinks)}")

        name = payload.name or _auto_name(payload.source_type, payload.source_path)
        instance_id = uuid.uuid4().hex
        try:
            instances.create_instance(
                instance_id, name, payload.source_type, payload.source_path, payload.sinks, _now()
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
        instances.rename_instance(instance_id, payload.name, _now())
        row = instances.get_instance(instance_id)
        assert row is not None  # can't vanish between the rename and this read
        return _to_instance_out(row)

    @router.delete("/{instance_id}", status_code=204)
    async def delete_instance(instance_id: str) -> None:
        if instances.get_instance(instance_id) is None:
            raise HTTPException(404, "Instance not found")
        instances.delete_instance(instance_id)

    return router
