"""Loom's FastAPI app (spec §4.2, §8).

    uvicorn app.main:create_app --factory

`create_app` opens the single shared SQLite operational store (spec §3)
each `JobRunner` job writes into, wires the Jobs API router over it, and
returns the app. It's a factory rather than a bare module-level `app` on
purpose: a module-level `app = create_app()` would open (and create, if
missing) the default on-disk `loom.sqlite3` as a side effect of merely
*importing* this module — including from `tests/test_api_jobs.py`, which
needs `create_app` to point at a throwaway `:memory:`/`tmp_path` database
instead. `ConfigsStore` has the same on-call-not-on-import shape: it
`mkdir`s `configs_dir` if missing, so `tests/test_api_configs.py` points it
at a throwaway `tmp_path` for the same reason.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import FastAPI

from app.api.configs import create_configs_router
from app.api.instances import create_instances_router
from app.api.jobs import create_jobs_router
from app.api.preview import create_preview_router
from app.configs.store import ConfigsStore
from app.jobs.runner import JobRunner
from app.jobs.store import connect
from app.pipeline.registry import SINKS, SOURCES
from app.pipeline.sinks.base import SinkAdapter

DEFAULT_DB_PATH = os.environ.get("LOOM_DB_PATH", "./loom.sqlite3")
DEFAULT_CONFIGS_DIR = os.environ.get("LOOM_CONFIGS_DIR", "./configs")
DEFAULT_FIXTURES_DIR = os.environ.get("LOOM_FIXTURES_DIR", "./tests/fixtures")


def create_app(
    db_path: str = DEFAULT_DB_PATH,
    configs_dir: str = DEFAULT_CONFIGS_DIR,
    fixtures_dir: str = DEFAULT_FIXTURES_DIR,
    sources: dict[str, tuple[type, Callable[[str], Any]]] = SOURCES,
    sinks: dict[str, Callable[[], SinkAdapter]] = SINKS,
) -> FastAPI:
    """`sources`/`sinks` default to the real registries but are injectable —
    the same seam `cli.run_ingest` and `JobRunner` leave at their defaults —
    so API-level tests can exercise a full job run with a recording fake
    sink instead of a live Neo4j."""
    conn = connect(db_path)
    runner = JobRunner(conn, sources=sources, sinks=sinks)
    configs_store = ConfigsStore(configs_dir)

    app = FastAPI(title="Loom")
    app.include_router(create_jobs_router(runner))
    app.include_router(create_instances_router(runner))
    app.include_router(create_configs_router(configs_store))
    app.include_router(create_preview_router(configs_store, fixtures_dir=fixtures_dir, sources=sources))
    return app
