"""The Neo4j sink (spec §4.1, §5, §6.1, §6.2). All Cypher here goes through
`db/neo4j_client.py` — this module never imports the bolt driver directly
(spec §6.5).

Node/relationship MERGE patterns match by `id` alone (no label baked into
the MERGE pattern) because a relationship can reference an endpoint whose
owning document hasn't been written yet in this same job — Pipeline.run
processes one document at a time (spec §4.1), so write order across
documents isn't guaranteed to match wikilink/reference order. Merging by
bare id first, then adding the label via `SET n:Label`, converges onto the
same node regardless of which write reaches it first — no fixed processing
order required, and no duplicate/orphaned placeholder nodes.

Setting a property to Python `None` removes it in Neo4j (assigning null is
equivalent to deleting the property) — this is exactly spec §5's "absent"
semantics for `rule_id` (explicit/curated) and `source_doc`/`content_hash`
(curated), so no extra branching is needed to honor it.
"""

from __future__ import annotations

import importlib
import re
from datetime import UTC, datetime
from itertools import groupby
from typing import Any, Callable, Protocol, Sequence, TypeVar

from app.db.neo4j_client import get_driver
from app.pipeline.sinks.base import SinkType
from app.pipeline.types import DeleteReport, Entity, ExtractionResult, OrphanFlag, Relationship, SinkReport

_T = TypeVar("_T")


class _SessionLike(Protocol):
    """The slice of `neo4j.Session` this sink needs — kept as a loose
    structural Protocol (not `from neo4j import Session`) so this module
    doesn't become a second door onto the bolt driver (spec §6.5). Loose
    on purpose: it only needs to admit both the real `neo4j.Session` and a
    fake test double, not fully describe either."""

    def run(self, *args: Any, **kwargs: Any) -> Any: ...
    def __enter__(self) -> Any: ...
    def __exit__(self, *exc: Any) -> Any: ...


class _DriverLike(Protocol):
    def session(self) -> _SessionLike: ...

_kg_schema = importlib.import_module("kg-schema")

_LABEL_RE = re.compile(r"[^A-Za-z0-9_]")
_REL_TYPE_RE = re.compile(r"[^A-Z_]")

# Every entity-type label a node could ever carry, sanitized once. Stripped
# before applying the current type's label on each write so a reclassified
# note (subfolder or keyword-signal changed on re-ingestion) converges onto
# a single label instead of accumulating the old one alongside the new.
_ALL_ENTITY_LABELS = ":".join(_LABEL_RE.sub("_", t) for t in _kg_schema.ENTITY_TYPES)


def _sanitize_label(label: str) -> str:
    return _LABEL_RE.sub("_", label)


def _sanitize_rel_type(rel_type: str) -> str:
    return _REL_TYPE_RE.sub("_", rel_type.upper())


def _entity_row(entity: Entity, result: ExtractionResult, now: str) -> dict[str, object]:
    return {
        "id": entity.id,
        "name": entity.name,
        "origin": entity.origin,
        "rule_id": entity.rule_id,
        "source_doc": result.doc_id,
        "content_hash": result.content_hash,
        "schema_version": _kg_schema.SCHEMA_VERSION,
        "now": now,
        "properties": dict(entity.properties),
    }


def _relationship_row(rel: Relationship, result: ExtractionResult, now: str) -> dict[str, object]:
    return {
        "from_id": rel.from_id,
        "to_id": rel.to_id,
        "origin": rel.origin,
        "rule_id": rel.rule_id,
        "source_doc": result.doc_id,
        "content_hash": result.content_hash,
        "schema_version": _kg_schema.SCHEMA_VERSION,
        "now": now,
        "properties": dict(rel.properties),
    }


def _entity_cypher(safe_label: str) -> str:
    # No curated-immunity guard here, unlike `_relationship_cypher`: nodes
    # can never be `origin: curated` in this system (spec §8 -- the only
    # API surface that creates curated elements is `POST /graph/relationships`,
    # which never creates nodes; "No node creation... in v1").
    return (
        "UNWIND $rows AS row\n"
        "MERGE (n {id: row.id})\n"
        "ON CREATE SET n.created_at = row.now\n"
        f"REMOVE n:{_ALL_ENTITY_LABELS}\n"
        f"SET n:{safe_label}\n"
        "SET n.name = row.name,\n"
        "    n.origin = row.origin,\n"
        "    n.rule_id = row.rule_id,\n"
        "    n.source_doc = row.source_doc,\n"
        "    n.content_hash = row.content_hash,\n"
        "    n.schema_version = row.schema_version,\n"
        "    n.updated_at = row.now,\n"
        "    n += row.properties"
    )


def _relationship_cypher(safe_type: str) -> str:
    # `MERGE ... ON MATCH SET` alone would unconditionally overwrite an
    # existing relationship's properties — including origin — silently
    # demoting a curated edge back to extracted/explicit the moment a
    # re-parse reproduces the same (from_id, to_id, type) (spec §6.2, the
    # "curated wins" rule applies to writes, not just deletes). The FOREACH
    # trick below only applies the property overwrite when the relationship
    # was NOT already curated at match time (`is_curated` is computed once,
    # right after MERGE, off the pre-write value of r.origin) — a curated
    # match falls through untouched, so the "duplicate is not written".
    # ON CREATE still sets everything unconditionally: a brand-new
    # relationship has no prior origin to protect.
    return (
        "UNWIND $rows AS row\n"
        "MERGE (src {id: row.from_id})\n"
        "MERGE (tgt {id: row.to_id})\n"
        f"MERGE (src)-[r:{safe_type}]->(tgt)\n"
        "ON CREATE SET r.created_at = row.now,\n"
        "    r.origin = row.origin,\n"
        "    r.rule_id = row.rule_id,\n"
        "    r.source_doc = row.source_doc,\n"
        "    r.content_hash = row.content_hash,\n"
        "    r.schema_version = row.schema_version,\n"
        "    r.updated_at = row.now,\n"
        "    r += row.properties\n"
        "WITH r, row, (r.origin = 'curated') AS is_curated\n"
        "FOREACH (_ IN CASE WHEN is_curated THEN [] ELSE [1] END |\n"
        "    SET r.origin = row.origin,\n"
        "        r.rule_id = row.rule_id,\n"
        "        r.source_doc = row.source_doc,\n"
        "        r.content_hash = row.content_hash,\n"
        "        r.schema_version = row.schema_version,\n"
        "        r.updated_at = row.now,\n"
        "        r += row.properties\n"
        ")"
    )


def _write_grouped_by_type(
    session: _SessionLike,
    items: Sequence[_T],
    type_of: Callable[[_T], str],
    sanitize: Callable[[str], str],
    row_of: Callable[[_T, ExtractionResult, str], dict[str, object]],
    cypher_for: Callable[[str], str],
    result: ExtractionResult,
    now: str,
) -> int:
    """Shared shape behind both entity and relationship writes: group by
    dynamic type, build parameter rows, run one query per type."""
    written = 0
    for type_name, group in groupby(sorted(items, key=type_of), key=type_of):
        rows = [row_of(item, result, now) for item in group]
        session.run(cypher_for(sanitize(type_name)), rows=rows)
        written += len(rows)
    return written


class Neo4jSink:
    sink_type: SinkType = "neo4j"

    def __init__(self, driver: _DriverLike | None = None) -> None:
        # `driver` is an injection seam for tests (a fake driver/session
        # double, since no live Neo4j is available to test against here);
        # production code leaves it unset and goes through the one door.
        self._driver = driver

    def _session(self) -> _SessionLike:
        if self._driver is not None:
            return self._driver.session()
        return get_driver().session()

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport:
        now = datetime.now(UTC).isoformat()

        with self._session() as session:
            nodes_written = _write_grouped_by_type(
                session,
                result.entities,
                lambda e: e.type,
                _sanitize_label,
                _entity_row,
                _entity_cypher,
                result,
                now,
            )
            relationships_written = _write_grouped_by_type(
                session,
                result.relationships,
                lambda r: r.type,
                _sanitize_rel_type,
                _relationship_row,
                _relationship_cypher,
                result,
                now,
            )

        return SinkReport(
            sink_type=self.sink_type,
            nodes_written=nodes_written,
            relationships_written=relationships_written,
        )

    def delete_non_curated_for_doc(self, doc_id: str) -> DeleteReport:
        with self._session() as session:
            # Directed match: every relationship this sink writes is created
            # directed (MERGE (src)-[r:TYPE]->(tgt)), so an undirected
            # pattern here would match — and DELETE — each one twice.
            rel_result = session.run(
                "MATCH ()-[r {source_doc: $doc_id}]->() "
                "WHERE r.origin <> 'curated' "
                "DELETE r "
                "RETURN count(r) AS c",
                doc_id=doc_id,
            ).single()

            # Nodes sourced from doc_id that are about to lose their only
            # justification for existing (below) but are still anchoring a
            # curated relationship survive that delete instead (spec §6.2)
            # — the node is kept alive purely to hold the curated edge up.
            # That's exactly an orphan (§6.3): the edge now rests on content
            # that no longer backs it. Detect these *before* running the
            # protective node delete below, since which nodes/edges count
            # as "still attached" doesn't change either way (curated edges
            # are never deleted), but keeping detection first keeps this
            # method's three queries in the same order as the reasoning.
            # Spec §6.3 asks for both halves: the edge is marked
            # `orphaned: true` in the graph itself (so the Graph page can
            # style it per spec §9) *and* surfaced in `JobResult` (via the
            # `OrphanFlag`s this query's rows become, below).
            orphan_result = session.run(
                "MATCH (n {source_doc: $doc_id}) "
                "WHERE n.origin <> 'curated' "
                "MATCH (n)-[cr]-() "
                "WHERE cr.origin = 'curated' "
                "SET cr.orphaned = true "
                "RETURN DISTINCT elementId(cr) AS edge_id",
                doc_id=doc_id,
            )
            orphans = tuple(
                OrphanFlag(
                    edge_id=record["edge_id"],
                    reason=(
                        f"endpoint node sourced from doc '{doc_id}' no longer "
                        "backed by that document's content after re-ingestion"
                    ),
                )
                for record in orphan_result
            )

            # A plain DETACH DELETE would cascade onto any origin=curated
            # relationship still attached to this node, violating curated
            # immunity (spec §6.2). Skip such nodes instead of deleting them
            # out from under a curated edge — the orphan_result query above
            # already flagged those edges rather than silently orphaning
            # them without a trace.
            node_result = session.run(
                "MATCH (n {source_doc: $doc_id}) "
                "WHERE n.origin <> 'curated' "
                "  AND NOT EXISTS { MATCH (n)-[cr]-() WHERE cr.origin = 'curated' } "
                "DETACH DELETE n "
                "RETURN count(n) AS c",
                doc_id=doc_id,
            ).single()

        rel_count = rel_result["c"] if rel_result else 0
        node_count = node_result["c"] if node_result else 0
        return DeleteReport(deleted_count=int(rel_count) + int(node_count), orphans=orphans)
