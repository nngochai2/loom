"""The preview endpoint (spec §4.1 design test, §7, §8; ticket #9):

    POST /configs/{id}/preview   {sample: fixture_id | uploaded file} -> ExtractionResult

Loads the requested config via `ConfigsStore` (the same store the Configs
API sits on), builds the same `SourceAdapter` a real job would (`SOURCES`),
and runs it through the exact same `Pipeline.run` the Jobs API's runner
uses -- the only difference is the sink: a `DryRunSink` that collects
instead of writing to Neo4j/ChromaDB. If preview ever needed a separate
extraction path, the `Pipeline.run` abstraction would have failed its
design test (`pipeline/core.py`); it doesn't, so this module is nothing
more than "resolve a directory + a target doc, then call Pipeline.run".

A "sample" is either a named fixture (`fixture_id`, a path relative to
`fixtures_dir/{docs,vault}` matching what that source type's adapter would
discover) or a one-off uploaded file, sent as `multipart/form-data` since
the two are mutually exclusive alternatives for the same logical field
rather than two endpoints. A fixture is resolved against its *entire*
source root (not an isolated copy of just that one file) so cross-document
structure a real job would see -- an Obsidian note's wikilinks to its
vault siblings, for instance -- resolves identically in preview and in a
real job over the same `source_path` (the acceptance criterion this ticket
asks a test to assert). An upload has no such siblings to resolve against,
so it's dropped into a throwaway directory of its own.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.configs.store import ConfigNotFoundError, ConfigsStore
from app.pipeline.core import Pipeline
from app.pipeline.registry import CONFIG_FROM_DICT, SOURCES
from app.pipeline.sinks.dryrun import DryRunSink
from app.pipeline.sources.obsidian import default_include_subfolder
from app.pipeline.types import ExtractionResult

# Where each source type's discoverable fixture samples live, relative to
# `fixtures_dir` -- the same fixture set `backend/tests/fixtures` already
# uses for the golden-parity gate (spec §11), reused here as the product's
# sample set too (spec §7: "user picks a fixture or uploads a sample doc").
_FIXTURE_SUBDIR_BY_SOURCE_TYPE: dict[str, str] = {
    "docx": "docs",
    "obsidian": "vault",
}

# The extension `discover()` globs for per source type (docx.py's
# `rglob("*.docx")`, obsidian.py's `rglob("*.md")`) -- an upload gets this
# extension appended if its filename lacks it, so it's actually
# discoverable in its own throwaway directory.
_UPLOAD_EXTENSION_BY_SOURCE_TYPE: dict[str, str] = {
    "docx": ".docx",
    "obsidian": ".md",
}


class EntityOut(BaseModel):
    id: str
    type: str
    name: str
    origin: str
    rule_id: str | None = None
    properties: dict[str, Any] = {}


class RelationshipOut(BaseModel):
    from_id: str
    to_id: str
    type: str
    origin: str
    rule_id: str | None = None
    properties: dict[str, Any] = {}


class ExtractionResultOut(BaseModel):
    doc_id: str
    content_hash: str
    entities: list[EntityOut]
    relationships: list[RelationshipOut]


def _to_extraction_result_out(result: ExtractionResult) -> ExtractionResultOut:
    return ExtractionResultOut(
        doc_id=result.doc_id,
        content_hash=result.content_hash,
        entities=[
            EntityOut(
                id=e.id, type=e.type, name=e.name, origin=e.origin,
                rule_id=e.rule_id, properties=dict(e.properties),
            )
            for e in result.entities
        ],
        relationships=[
            RelationshipOut(
                from_id=r.from_id, to_id=r.to_id, type=r.type, origin=r.origin,
                rule_id=r.rule_id, properties=dict(r.properties),
            )
            for r in result.relationships
        ],
    )


def _relative_posix(path: str, root: Path) -> str:
    return Path(path).resolve().relative_to(root.resolve()).as_posix()


def _run_dryrun_for_doc(adapter: Any, source_root: str, config: Any, target_doc_id: str) -> ExtractionResult:
    """Run `Pipeline.run` (no `store`, so a full extraction pass -- the same
    shape `cli.py` uses without `--db`) with a `DryRunSink` as the only
    sink, then pull out the one doc the caller actually wants."""
    sink = DryRunSink()
    job_result = Pipeline().run(
        source=adapter,
        source_path=source_root,
        sinks=[sink],
        config=config,
        progress=lambda doc_id, fraction: None,
    )
    if target_doc_id in sink.results:
        return sink.results[target_doc_id]

    status = next((s for s in job_result.doc_statuses if s.doc_id == target_doc_id), None)
    error = status.error if status is not None and status.error else "extraction produced no result"
    raise HTTPException(422, f"Preview extraction failed: {error}")


def _place_upload(tmp_dir: Path, source_type: str, config: Any, filename: str, content: bytes) -> None:
    """Write an uploaded sample into its own throwaway directory so it's
    discoverable by that source type's adapter. Obsidian's `discover()`
    only picks up notes under a configured folder (spec ADR-0004), so the
    upload is nested under the config's first `include_folders` entry (or
    `main_folder` if none) to satisfy that filter the same way a real note
    living in the vault would."""
    ext = _UPLOAD_EXTENSION_BY_SOURCE_TYPE[source_type]
    name = Path(filename).name or "sample"
    if not name.endswith(ext):
        name = f"{name}{ext}"

    subdir = default_include_subfolder(config) if source_type == "obsidian" else ""

    target_dir = tmp_dir / subdir if subdir else tmp_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / name).write_bytes(content)


def create_preview_router(
    store: ConfigsStore,
    fixtures_dir: str,
    sources: dict[str, tuple[type, Callable[[str], Any]]] = SOURCES,
    config_from_dict: dict[str, Callable[[dict[str, Any]], Any]] = CONFIG_FROM_DICT,
) -> APIRouter:
    """`sources`/`config_from_dict` default to the real registries but are
    injectable, the same seam `create_jobs_router`'s `JobRunner` leaves at
    its defaults, so tests can preview against a fake source without a
    real fixtures directory."""
    router = APIRouter(prefix="/configs", tags=["configs"])

    @router.post("/{config_id}/preview", response_model=ExtractionResultOut)
    async def preview_config(
        config_id: str,
        fixture_id: str | None = Form(default=None),
        sample: UploadFile | None = File(default=None),
    ) -> ExtractionResultOut:
        if (fixture_id is None) == (sample is None):
            raise HTTPException(422, "Provide exactly one of fixture_id or sample (uploaded file)")

        try:
            detail = store.get_config(config_id)
        except ConfigNotFoundError as exc:
            raise HTTPException(404, "Config not found") from exc

        source_entry = sources.get(detail.source_type)
        build_config = config_from_dict.get(detail.source_type)
        if source_entry is None or build_config is None:
            raise HTTPException(422, f"No pipeline support for source_type {detail.source_type!r}")
        adapter_cls, _path_loader = source_entry

        config = build_config(detail.data)
        adapter = adapter_cls(config)

        if fixture_id is not None:
            subdir = _FIXTURE_SUBDIR_BY_SOURCE_TYPE.get(detail.source_type)
            source_root = Path(fixtures_dir) / subdir if subdir else None
            if source_root is None or not source_root.is_dir():
                raise HTTPException(
                    422, f"No fixtures available for source_type {detail.source_type!r}"
                )

            docs = adapter.discover(str(source_root))
            target = next(
                (d for d in docs if _relative_posix(d.path, source_root) == fixture_id), None
            )
            if target is None:
                raise HTTPException(404, f"Fixture {fixture_id!r} not found")

            result = _run_dryrun_for_doc(adapter, str(source_root), config, target.doc_id)
            return _to_extraction_result_out(result)

        assert sample is not None
        content = await sample.read()
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _place_upload(tmp_path, detail.source_type, config, sample.filename or "sample", content)

            docs = adapter.discover(str(tmp_path))
            if not docs:
                raise HTTPException(
                    422,
                    f"Uploaded sample was not recognized as a {detail.source_type!r} document",
                )
            target = docs[0]

            result = _run_dryrun_for_doc(adapter, str(tmp_path), config, target.doc_id)
            return _to_extraction_result_out(result)

    return router
