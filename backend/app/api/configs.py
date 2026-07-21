"""The Configs API router (spec §7, §8): CRUD over parsing-rule config YAML
on disk, validated against the docx rule-file JSON Schema
(`pipeline/rules/schema.py`) or the Obsidian source-config JSON Schema
(`pipeline/sources/obsidian.py`, ADR-0004) depending on a config's detected
source type.

    GET    /configs            -> list rule sets (id, source_type, title)
    GET    /configs/{id}       -> full config as parsed YAML + its JSON Schema
    POST   /configs            {id, source_type, data} -> validated create
    PUT    /configs/{id}       {data} -> validated update

YAML on disk stays the source of truth (spec §7) -- this router only
translates HTTP in and out of `ConfigsStore`, which is the one thing that
actually touches the filesystem.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.configs.store import (
    ConfigAlreadyExistsError,
    ConfigDetail,
    ConfigNotFoundError,
    ConfigSchemaValidationError,
    ConfigSummary,
    ConfigsStore,
    InvalidConfigIdError,
)


class ConfigSummaryOut(BaseModel):
    id: str
    source_type: str
    title: str


class ConfigDetailOut(BaseModel):
    id: str
    source_type: str
    title: str
    data: dict[str, Any]
    json_schema: dict[str, Any]


class ConfigListResponse(BaseModel):
    configs: list[ConfigSummaryOut]


class CreateConfigRequest(BaseModel):
    id: str
    source_type: str
    data: dict[str, Any]


class UpdateConfigRequest(BaseModel):
    data: dict[str, Any]


def _to_summary_out(summary: ConfigSummary) -> ConfigSummaryOut:
    return ConfigSummaryOut(id=summary.id, source_type=summary.source_type, title=summary.title)


def _to_detail_out(detail: ConfigDetail) -> ConfigDetailOut:
    return ConfigDetailOut(
        id=detail.id,
        source_type=detail.source_type,
        title=detail.title,
        data=detail.data,
        json_schema=detail.json_schema,
    )


def _schema_error_detail(exc: ConfigSchemaValidationError) -> dict[str, Any]:
    return {"errors": [{"path": e.path, "message": e.message} for e in exc.errors]}


def create_configs_router(store: ConfigsStore) -> APIRouter:
    router = APIRouter(prefix="/configs", tags=["configs"])

    @router.get("", response_model=ConfigListResponse)
    async def list_configs() -> ConfigListResponse:
        return ConfigListResponse(configs=[_to_summary_out(s) for s in store.list_configs()])

    @router.get("/{config_id}", response_model=ConfigDetailOut)
    async def get_config(config_id: str) -> ConfigDetailOut:
        try:
            detail = store.get_config(config_id)
        except ConfigNotFoundError as exc:
            raise HTTPException(404, "Config not found") from exc
        return _to_detail_out(detail)

    @router.post("", response_model=ConfigDetailOut, status_code=201)
    async def create_config(payload: CreateConfigRequest) -> ConfigDetailOut:
        try:
            detail = store.create_config(payload.id, payload.source_type, payload.data)
        except InvalidConfigIdError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ConfigAlreadyExistsError as exc:
            raise HTTPException(409, f"Config {payload.id!r} already exists") from exc
        except ConfigSchemaValidationError as exc:
            raise HTTPException(422, _schema_error_detail(exc)) from exc
        return _to_detail_out(detail)

    @router.put("/{config_id}", response_model=ConfigDetailOut)
    async def update_config(config_id: str, payload: UpdateConfigRequest) -> ConfigDetailOut:
        try:
            detail = store.update_config(config_id, payload.data)
        except ConfigNotFoundError as exc:
            raise HTTPException(404, "Config not found") from exc
        except ConfigSchemaValidationError as exc:
            raise HTTPException(422, _schema_error_detail(exc)) from exc
        return _to_detail_out(detail)

    return router
