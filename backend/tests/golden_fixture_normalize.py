"""Pure normalization + diff logic for the golden-fixture parity gate
(spec §10 Phase 1 gate, ADR-0007).

Two callers share this module:

- `backend/scripts/generate_golden_fixture_snapshot.py` (a one-off, local
  -only tool — see its docstring) calls the `normalize_naa_*` functions on
  objects from NAA's real parser to build the committed golden snapshot
  (`tests/fixtures/golden/*.json`).
- `backend/tests/test_golden_fixture_parity.py` (which runs in CI) calls
  the `normalize_loom_*` functions on Loom's own adapters' output and
  diffs the result against the committed snapshot via `diff_records`.

This module itself imports nothing from NAA — the `normalize_naa_*`
functions take duck-typed `Any` objects (NAA's `Note`/`WikiLink`/
`ParsedItem` dataclasses) precisely so this file stays importable in CI,
where NAA's source tree (`D:\\Cloned Projects\\NAA`, a machine-local
sibling repo) does not exist.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from app.pipeline.types import Entity, Relationship

# ── Intentional divergences (acceptance criterion 3) ──────────────────────
#
# Loom's fixture docx rule file (tests/fixtures/br_requirements.yml) is a
# deliberate re-derivation of NAA's real parsing-rules/br_requirements.yml
# with the project-specific "BR" node_label swapped for kg-schema's generic
# REQUIREMENT entity type (ADR-0006: Loom's core has no project-specific
# entity types, only the generic parent-link mechanism). The generator
# script runs NAA's parser with NAA's *actual* rule file (its real current
# behavior) and Loom's adapter with Loom's *actual* fixture rule file (what
# Loom ships) — so this one label swap is an expected, allowlisted mismatch,
# not a bug. Anything else that doesn't match is a real regression.
DOCX_NODE_LABEL_ALLOWLIST: dict[str, str] = {"BR": "REQUIREMENT"}

# Fields Loom's schema contract adds that NAA has no concept of (spec §5:
# every extracted element carries `origin` + `rule_id`) — deliberately
# excluded from comparison, per acceptance criterion 1, rather than
# silently included and left to always mismatch.
LOOM_ONLY_ENTITY_FIELDS = ("origin", "rule_id")
LOOM_ONLY_RELATIONSHIP_FIELDS = ("origin", "rule_id")


# ── Obsidian: entities (one per note) ──────────────────────────────────────


def naa_note_id(note: Any) -> str:
    """The comparable id for an NAA `Note`.

    Deliberately *not* `note.node_id` (NAA's own property): that hashes
    `str(note.path)`, which uses the OS-native separator, so the same
    vault would hash differently on Windows (where the golden snapshot is
    generated) vs. Linux (where CI runs). Loom's adapter hashes
    `relative_path.as_posix()` for exactly this portability reason, so
    the comparable NAA-side id is computed the same way here rather than
    by calling into NAA's (intentionally left alone) property.
    """
    return hashlib.sha1(note.path.as_posix().encode()).hexdigest()[:16]


def normalize_loom_obsidian_entity(entity: Entity) -> dict[str, Any]:
    """Loom `Entity` -> comparable dict.

    `entity.id` is expected to equal `naa_note_id(note)` exactly: both are
    `sha1(relative_path.as_posix())[:16]`.
    """
    props = entity.properties
    return {
        "id": entity.id,
        "type": entity.type,
        "name": entity.name,
        "subfolder": props.get("subfolder", ""),
        "status": props.get("status", ""),
        # NAA's field is called `created_at`; Loom renamed it
        # `header_created_at` (same header-parsed value, disambiguated
        # from any future ingestion-time timestamp) — allowlisted here.
        "created_at": props.get("header_created_at", ""),
    }


def normalize_naa_obsidian_note(note: Any) -> dict[str, Any]:
    """NAA `Note` -> the same comparable dict shape."""
    return {
        "id": naa_note_id(note),
        "type": note.note_type,
        "name": note.title,
        "subfolder": note.subfolder,
        "status": note.status,
        "created_at": note.created_at,
    }


# ── Obsidian: explicit wikilink edges ──────────────────────────────────────


def normalize_loom_obsidian_edge(rel: Relationship) -> dict[str, Any]:
    return {
        "from_id": rel.from_id,
        "to_id": rel.to_id,
        "type": rel.type,
        "alias": rel.properties.get("alias", ""),
        "context": rel.properties.get("context", ""),
    }


def normalize_naa_obsidian_edge(from_id: str, to_id: str, link: Any) -> dict[str, Any]:
    return {
        "from_id": from_id,
        "to_id": to_id,
        "type": link.relationship,
        "alias": link.alias,
        "context": link.context,
    }


def edge_key(edge: Mapping[str, Any]) -> tuple[Any, ...]:
    return (edge["from_id"], edge["to_id"], edge["type"])


def entity_key(entity: Mapping[str, Any]) -> Any:
    return entity["id"]


# ── Docx: requirement entities (one per id-matching table row) ─────────────


def normalize_loom_docx_entity(entity: Entity) -> dict[str, Any]:
    props = entity.properties
    # `Entity.properties` is typed `dict[str, object]` (spec §5's schema
    # contract keeps it deliberately loose); the docx rule engine always
    # populates these two keys with `list[str]` / `dict[str, list[str]]`
    # respectively (app/pipeline/rules/engine.py's `apply()`), so the casts
    # below just recover that known shape for comparison purposes.
    categories = cast("list[str]", props["candidate_categories"])
    named_extractions = cast("dict[str, list[str]]", props["named_extractions"])
    return {
        "type": entity.type,
        "req_id": props["req_id"],
        "title": entity.name,
        "body": props["body"],
        "source_file": props["source_file"],
        # Order is "display priority in the UI" (NAA's own comment), not
        # a semantic distinction extraction correctness depends on, so
        # it's sorted away here rather than compared positionally.
        "candidate_categories": sorted(categories),
        "named_extractions": {k: list(v) for k, v in named_extractions.items()},
    }


def normalize_naa_docx_item(item: Any) -> dict[str, Any]:
    mapped_type = DOCX_NODE_LABEL_ALLOWLIST.get(item.node_label, item.node_label)
    return {
        "type": mapped_type,
        "req_id": item.req_id,
        "title": item.title,
        "body": item.body,
        "source_file": item.source_file,
        "candidate_categories": sorted(item.candidate_categories),
        "named_extractions": {k: list(v) for k, v in item.named_extractions.items()},
    }


def docx_entity_key(entity: Mapping[str, Any]) -> Any:
    # Loom's node_id hashes `node_label::doc_id::req_id` (doc_id is a
    # content-addressed hash of the file's relative path); NAA's hashes
    # `node_label::source_label::source_file::req_id`. The two schemes are
    # structurally different by design (ADR-0006 lifted no shared
    # document-identity concept), so raw ids are never compared — only
    # this (source_file, req_id) pair, which both sides can derive.
    return (entity["source_file"], entity["req_id"])


# ── Diff ─────────────────────────────────────────────────────────────────


def diff_records(
    golden: Sequence[Mapping[str, Any]],
    actual: Sequence[Mapping[str, Any]],
    key_fn: Callable[[Mapping[str, Any]], Any],
    label: str,
) -> list[str]:
    """Compare two lists of normalized records keyed by `key_fn`.

    Returns human-readable mismatch descriptions; an empty list means the
    two sides match. Pure and synthetic-data-testable by design — no I/O,
    no NAA/Loom-specific types beyond plain mappings.
    """
    golden_by_key = {key_fn(r): r for r in golden}
    actual_by_key = {key_fn(r): r for r in actual}

    problems: list[str] = []
    for k in sorted(golden_by_key.keys() - actual_by_key.keys(), key=repr):
        problems.append(f"{label}: NAA produced {k!r} but Loom's adapter did not")
    for k in sorted(actual_by_key.keys() - golden_by_key.keys(), key=repr):
        problems.append(f"{label}: Loom's adapter produced {k!r} but NAA did not")
    for k in sorted(golden_by_key.keys() & actual_by_key.keys(), key=repr):
        g, a = golden_by_key[k], actual_by_key[k]
        if g != a:
            problems.append(f"{label} {k!r} mismatch:\n  NAA:  {g}\n  Loom: {a}")
    return problems
