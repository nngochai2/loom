"""JSON Schema for docx parsing-rule files (spec §7; ADR-0001, ADR-0005).

The rule-file shape is lifted from NAA's real rule file
(`NAA/parsing-rules/br_requirements.yml`, ADR-0001): `id_pattern`/
`id_format`, `title_from`, `category_signals`, `named_extractions`,
`context` collection flags. Not chunking parameters. Regex rules take no
LLM prompts and remain deterministic pattern matching (ADR-0001); the
opt-in `context.prose_extraction` block below is the one exception,
scoped to prose content only (ADR-0018).

Each `category_signal`/`named_extraction` entry carries a stable,
generated `id` distinct from its editable `name` (ADR-0005) — the future
join key for `corrections.originating_rule_id` (spec §6.4). Nothing
consumes the id yet; `RULE_FILE_SCHEMA` and `validate_rule_file` just make
sure every rule file has one, and that it's unique, so the join key is
correct from day one. `context.prose_extraction`'s `id` shares this same
id-namespace (ADR-0018) — it becomes the `rule_id` stamped on every
entity/relationship the prose-extraction path produces.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

_kg_schema = importlib.import_module("kg-schema")

_STABLE_ID_ENTRY_PROPERTIES: dict[str, Any] = {
    "id": {"type": "string", "minLength": 1},
    "name": {"type": "string", "minLength": 1},
    "pattern": {"type": "string", "minLength": 1},
    "flags": {"type": "string"},
}

CATEGORY_SIGNAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "name", "pattern"],
    "properties": _STABLE_ID_ENTRY_PROPERTIES,
    "additionalProperties": False,
}

NAMED_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "name", "pattern"],
    "properties": {
        **_STABLE_ID_ENTRY_PROPERTIES,
        "group": {"type": "integer", "minimum": 0},
        "transform": {"type": "string", "enum": ["", "uppercase", "lowercase"]},
        "filter": {"type": "string", "enum": ["", "no_spaces"]},
        "deduplicate": {"type": "boolean"},
        "sort": {"type": "boolean"},
    },
    "additionalProperties": False,
}

PROSE_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id"],
    "properties": {
        "enabled": {"type": "boolean"},
        "id": {"type": "string", "minLength": 1},
        "target_entity_types": {
            "type": "array",
            "items": {"type": "string", "enum": list(_kg_schema.ENTITY_TYPES)},
        },
        "target_relationship_types": {
            "type": "array",
            "items": {"type": "string", "enum": list(_kg_schema.RELATIONSHIP_TYPES)},
        },
    },
    "additionalProperties": False,
}

RULE_FILE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Loom docx parsing rule file",
    "type": "object",
    "required": ["name", "node_label", "id_pattern"],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "node_label": {"type": "string", "minLength": 1},
        "id_pattern": {"type": "string", "minLength": 1},
        "id_flags": {"type": "string"},
        "id_format": {"type": "string"},
        "title_from": {"type": "string", "minLength": 1},
        "category_signals": {"type": "array", "items": CATEGORY_SIGNAL_SCHEMA},
        "named_extractions": {"type": "array", "items": NAMED_EXTRACTION_SCHEMA},
        "context": {
            "type": "object",
            "properties": {
                "include_paragraphs": {"type": "boolean"},
                "include_non_br_tables": {"type": "boolean"},
                "prose_extraction": PROSE_EXTRACTION_SCHEMA,
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class CategorySignal:
    """One `category_signals` entry: a regex evaluated against an item's
    body; all matching signals are collected (not first-match-wins)."""

    id: str
    name: str
    pattern: str
    flags: str = ""


@dataclass(frozen=True)
class NamedExtraction:
    """One `named_extractions` entry: a regex producing `{name: [values]}`
    per item, with optional group selection, value transform/filter, and
    dedup/sort post-processing."""

    id: str
    name: str
    pattern: str
    group: int = 0
    transform: str = ""
    filter: str = ""
    deduplicate: bool = False
    sort: bool = False
    flags: str = ""


@dataclass(frozen=True)
class ProseExtraction:
    """The opt-in `context.prose_extraction` block (ADR-0018): a local-LLM
    extraction pass over a docx document's already-collected prose text
    (`context.include_paragraphs`/`include_non_br_tables`), run alongside
    — not instead of — the regex path above. Disabled by default; an
    absent block parses to this dataclass's defaults, so existing rule
    files are unaffected.

    `id` follows the same stable-id pattern as `category_signals`/
    `named_extractions` (ADR-0005) and becomes the `rule_id` stamped on
    every entity/relationship this path produces. `target_entity_types`/
    `target_relationship_types` scope the LLM to a specific subset of
    `kg-schema`'s enum rather than the full vocabulary (ADR-0018)."""

    enabled: bool = False
    id: str = ""
    target_entity_types: tuple[str, ...] = ()
    target_relationship_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleContext:
    include_paragraphs: bool = True
    include_non_br_tables: bool = True
    prose_extraction: ProseExtraction = field(default_factory=ProseExtraction)


@dataclass(frozen=True)
class RuleFile:
    """A loaded, structured rule file — the parsed counterpart of the YAML
    on disk (spec §7's source of truth)."""

    name: str
    node_label: str
    id_pattern: str
    id_format: str = "{}"
    id_flags: str = ""
    title_from: str = "first_line"
    category_signals: tuple[CategorySignal, ...] = ()
    named_extractions: tuple[NamedExtraction, ...] = ()
    context: RuleContext = field(default_factory=RuleContext)


def validate_rule_file(raw: dict[str, Any]) -> None:
    """Validate a raw (YAML-loaded) rule file against `RULE_FILE_SCHEMA`
    and ADR-0005's id-stability requirement. Raises on the first problem
    found, same as `jsonschema.validate`."""
    Draft202012Validator(RULE_FILE_SCHEMA).validate(raw)
    _check_stable_ids_are_unique(raw)


def _check_stable_ids_are_unique(raw: dict[str, Any]) -> None:
    entries = [*raw.get("category_signals", []), *raw.get("named_extractions", [])]
    prose_extraction_raw = raw.get("context", {}).get("prose_extraction")
    if prose_extraction_raw is not None:
        entries.append(prose_extraction_raw)

    seen: set[str] = set()
    for entry in entries:
        entry_id = entry["id"]
        if entry_id in seen:
            raise ValueError(
                f"duplicate rule id {entry_id!r} in rule file {raw.get('name', '')!r} — "
                "ids must be unique (ADR-0005), they are the correction-analytics join key"
            )
        seen.add(entry_id)


def rule_file_from_dict(raw: dict[str, Any]) -> RuleFile:
    """Build a validated `RuleFile` from an already-loaded dict -- the
    dict-input counterpart to `load_rule_file`'s path-input, so a preview
    run built from `ConfigsStore` data (`app/api/preview.py`, ticket #9)
    and a job/CLI run built from a path on disk go through the exact same
    validation and construction and can never drift apart."""
    validate_rule_file(raw)

    context_raw = raw.get("context", {})
    prose_extraction_raw = context_raw.get("prose_extraction")
    prose_extraction = (
        ProseExtraction(
            enabled=prose_extraction_raw.get("enabled", False),
            id=prose_extraction_raw["id"],
            target_entity_types=tuple(prose_extraction_raw.get("target_entity_types", [])),
            target_relationship_types=tuple(
                prose_extraction_raw.get("target_relationship_types", [])
            ),
        )
        if prose_extraction_raw is not None
        else ProseExtraction()
    )

    return RuleFile(
        name=raw["name"],
        node_label=raw["node_label"],
        id_pattern=raw["id_pattern"],
        id_format=raw.get("id_format", "{}"),
        id_flags=raw.get("id_flags", ""),
        title_from=raw.get("title_from", "first_line"),
        category_signals=tuple(
            CategorySignal(id=s["id"], name=s["name"], pattern=s["pattern"], flags=s.get("flags", ""))
            for s in raw.get("category_signals", [])
        ),
        named_extractions=tuple(
            NamedExtraction(
                id=e["id"],
                name=e["name"],
                pattern=e["pattern"],
                group=e.get("group", 0),
                transform=e.get("transform", ""),
                filter=e.get("filter", ""),
                deduplicate=e.get("deduplicate", False),
                sort=e.get("sort", False),
                flags=e.get("flags", ""),
            )
            for e in raw.get("named_extractions", [])
        ),
        context=RuleContext(
            include_paragraphs=context_raw.get("include_paragraphs", True),
            include_non_br_tables=context_raw.get("include_non_br_tables", True),
            prose_extraction=prose_extraction,
        ),
    )


def load_rule_file(path: str) -> RuleFile:
    """Load and validate a docx rule file from YAML (spec §7)."""
    with Path(path).open(encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    return rule_file_from_dict(raw)
