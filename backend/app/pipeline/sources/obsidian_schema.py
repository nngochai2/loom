"""JSON Schema for Obsidian source configs (spec §7; ADR-0004).

Describes `ObsidianSourceConfig`'s shape (`sources/obsidian.py`) — the
classification config NAA hardcoded in `pipeline/src/config.py` (folder->type
map, keyword signals, relationship-inference keywords, included folders)
moved into per-vault YAML instead (ADR-0004). Lives in its own module
rather than alongside `ObsidianSourceConfig`/`load_config` because its only
consumer is the Configs API's validated write path (`app/configs/store.py`)
— `load_config` itself is unchanged and doesn't validate on load, matching
its behavior before this schema existed.

`name` is optional and display-only (the Configs API falls back to the
config's id when absent); every other field mirrors `ObsidianSourceConfig`.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

OBSIDIAN_CONFIG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Loom Obsidian source config",
    "type": "object",
    "required": [
        "include_folders",
        "tags_folder",
        "main_folder",
        "subfolder_type_map",
        "type_signals",
        "rel_keywords",
    ],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "include_folders": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "tags_folder": {"type": "string", "minLength": 1},
        "main_folder": {"type": "string", "minLength": 1},
        "subfolder_type_map": {
            "type": "object",
            "additionalProperties": {"type": "string", "minLength": 1},
        },
        "type_signals": {
            "type": "object",
            "additionalProperties": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
        },
        "rel_keywords": {
            "type": "object",
            "additionalProperties": {"type": "string", "minLength": 1},
        },
    },
    "additionalProperties": False,
}


def validate_obsidian_config(raw: dict[str, Any]) -> None:
    """Validate a raw (YAML-loaded) Obsidian source config against
    `OBSIDIAN_CONFIG_SCHEMA`. Raises on the first problem found, same as
    `jsonschema.validate` (mirrors `rules/schema.py`'s `validate_rule_file`)."""
    Draft202012Validator(OBSIDIAN_CONFIG_SCHEMA).validate(raw)
