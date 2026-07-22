"""Shared value types passed between pipeline stages (spec §4.1).

These are plain data — no behavior. `SourceAdapter.load()` produces a
`LoadedDoc`; extraction + the rule engine turn that into an
`ExtractionResult`; `SinkAdapter.write()` consumes the result and reports
a `SinkReport`; `Pipeline.run()` accumulates per-doc outcomes into a
`JobResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Origin = Literal["extracted", "explicit", "curated"]
DocOutcome = Literal["skipped", "updated", "failed", "removed"]


@dataclass(frozen=True)
class ExtractionVersion:
    """A source's LLM extraction "fingerprint" for one `Pipeline.run` call:
    the prompt-template version and model name that produced (or will
    produce) that run's prose-derived elements (ADR-0020).

    Compared alongside `content_hash` when deciding whether to skip a
    document (`Pipeline.run`): unchanged content plus an unchanged
    fingerprint skips re-extraction as usual; either one changing forces
    the same delete-then-rewrite path already used for a content change,
    so curated edges and tombstones are unaffected either way. `None`
    where a source has no such concept (e.g. Obsidian, or a docx rule file
    with prose extraction disabled) — behavior then reduces to comparing
    `content_hash` alone, exactly as it did before this existed.
    """

    prompt_version: str
    model: str


@dataclass(frozen=True)
class SourceDoc:
    """One document as enumerated by `SourceAdapter.discover()`."""

    doc_id: str
    path: str
    content_hash: str


@dataclass(frozen=True)
class ExplicitEdge:
    """A relationship asserted by the source itself (e.g. an Obsidian wikilink).

    Emitted directly by `SourceAdapter.load()`, tagged `origin: explicit` —
    bypasses rule-engine inference entirely.
    """

    from_id: str
    to_id: str
    type: str
    properties: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedDoc:
    """One document's text + structural metadata, ready for extraction."""

    doc: SourceDoc
    content: str
    explicit_edges: tuple[ExplicitEdge, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Entity:
    id: str
    type: str
    name: str
    origin: Origin
    rule_id: str | None = None
    properties: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Relationship:
    from_id: str
    to_id: str
    type: str
    origin: Origin
    rule_id: str | None = None
    properties: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionResult:
    """Output of extraction + rule engine for one document; the shape
    `SinkAdapter.write()` consumes and `DryRunSink` collects verbatim.

    `content_hash` travels with the result (not as a separate `write()`
    parameter) so a sink can stamp the spec §5 mandatory properties from
    `result` alone, matching `SinkAdapter.write(doc_id, result)`'s signature.

    `warning` (ADR-0022) is set when part of extraction degraded instead of
    failing outright — currently only prose extraction hitting an
    unreachable/timed-out Ollama or an unusable response, while `entities`/
    `relationships` still carry whatever *did* succeed (the regex engine's
    output). `None` means nothing degraded.
    """

    doc_id: str
    content_hash: str = ""
    entities: tuple[Entity, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    warning: str | None = None


@dataclass(frozen=True)
class SinkReport:
    sink_type: str
    nodes_written: int = 0
    relationships_written: int = 0


@dataclass(frozen=True)
class OrphanFlag:
    """A curated edge whose endpoint disappeared on re-ingestion (§6.3).

    Never auto-deleted — surfaced here so a human can resolve it in the
    Graph page.
    """

    edge_id: str
    reason: str


@dataclass(frozen=True)
class DeleteReport:
    """Return shape of `SinkAdapter.delete_non_curated_for_doc` (§6.1, §6.2).

    A bare `int` count couldn't carry *which* curated edges the delete left
    resting on now-gone content — `deleted_count` keeps the old signal,
    `orphans` carries the new one so `Pipeline.run` can fold it straight
    into `JobResult.orphans` without a second query round-trip of its own.
    """

    deleted_count: int
    orphans: tuple[OrphanFlag, ...] = ()


@dataclass(frozen=True)
class DocStatus:
    """`warning` (ADR-0022) carries a non-fatal degradation for an
    otherwise-`updated` doc (a prose-extraction failure whose regex output
    still wrote) — distinct from `error`, which only ever accompanies
    `outcome="failed"`. Surfaced the same way `JobResult.orphans` is meant
    to be: expandable per-row detail in the Ingest results table, not a
    reason to treat the whole doc as failed."""

    doc_id: str
    outcome: DocOutcome
    error: str | None = None
    warning: str | None = None


@dataclass
class JobResult:
    doc_statuses: list[DocStatus] = field(default_factory=list)
    orphans: list[OrphanFlag] = field(default_factory=list)
