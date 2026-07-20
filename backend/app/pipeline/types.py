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
    properties: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Relationship:
    from_id: str
    to_id: str
    type: str
    origin: Origin
    rule_id: str | None = None


@dataclass(frozen=True)
class ExtractionResult:
    """Output of extraction + rule engine for one document; the shape
    `SinkAdapter.write()` consumes and `DryRunSink` collects verbatim."""

    doc_id: str
    entities: tuple[Entity, ...] = ()
    relationships: tuple[Relationship, ...] = ()


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
class DocStatus:
    doc_id: str
    outcome: DocOutcome
    error: str | None = None


@dataclass
class JobResult:
    doc_statuses: list[DocStatus] = field(default_factory=list)
    orphans: list[OrphanFlag] = field(default_factory=list)
