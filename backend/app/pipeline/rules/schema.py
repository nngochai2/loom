"""JSON Schema for docx parsing-rule files (spec §7; ADR-0001, ADR-0005).

The rule-file shape is lifted from NAA's real rule file
(`NAA/parsing-rules/br_requirements.yml`, ADR-0001): `id_pattern`/
`id_format`, `title_from`, `category_signals`, `named_extractions`,
`context` collection flags. Not chunking parameters, not LLM prompts —
extraction is deterministic pattern matching.

Each `category_signal`/`named_extraction` entry carries a stable,
generated `id` distinct from its editable `name` (ADR-0005) — the future
join key for `corrections.originating_rule_id` (spec §6.4). Nothing
consumes the id yet; `RULE_FILE_SCHEMA` and `validate_rule_file` just make
sure every rule file has one, and that it's unique, so the join key is
correct from day one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

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
class RuleContext:
    include_paragraphs: bool = True
    include_non_br_tables: bool = True


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
    seen: set[str] = set()
    for entry in [*raw.get("category_signals", []), *raw.get("named_extractions", [])]:
        entry_id = entry["id"]
        if entry_id in seen:
            raise ValueError(
                f"duplicate rule id {entry_id!r} in rule file {raw.get('name', '')!r} — "
                "ids must be unique (ADR-0005), they are the correction-analytics join key"
            )
        seen.add(entry_id)


def load_rule_file(path: str) -> RuleFile:
    """Load and validate a docx rule file from YAML (spec §7)."""
    with Path(path).open(encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    validate_rule_file(raw)

    context_raw = raw.get("context", {})
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
        ),
    )
