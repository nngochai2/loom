"""Loom's versioned graph schema contract.

`schema.json` and `VERSION` are the source of truth (spec §5); everything
exported here is derived from them at import time so there is no second
copy of the vocabulary to keep in sync by hand. This package has no
imports from `app/` — it is designed to be lifted out into a standalone
package the day NAA's MCP server needs to import it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
_SCHEMA_PATH = _ROOT / "schema.json"
_VERSION_PATH = _ROOT / "VERSION"


def _load_schema() -> dict[str, Any]:
    with _SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


_schema = _load_schema()

SCHEMA_VERSION: str = _VERSION_PATH.read_text(encoding="utf-8").strip()
ENTITY_TYPES: tuple[str, ...] = tuple(_schema["entity_types"])
RELATIONSHIP_TYPES: tuple[str, ...] = tuple(_schema["relationship_types"])
DEFAULT_RELATIONSHIP_TYPE: str = _schema["default_relationship_type"]
MANDATORY_PROPERTIES: tuple[dict[str, Any], ...] = tuple(_schema["mandatory_properties"])
MANDATORY_PROPERTY_NAMES: tuple[str, ...] = tuple(p["name"] for p in MANDATORY_PROPERTIES)

__all__ = [
    "SCHEMA_VERSION",
    "ENTITY_TYPES",
    "RELATIONSHIP_TYPES",
    "DEFAULT_RELATIONSHIP_TYPE",
    "MANDATORY_PROPERTIES",
    "MANDATORY_PROPERTY_NAMES",
]
