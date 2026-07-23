"""Shared source_type/sink validation — both the Jobs and Instances routers
need to check a (source_type, sinks) pair against the real registries
before acting on it, so it lives in one place rather than two copies.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def validate_source_and_sinks(
    sources: dict[str, Any], sinks_registry: dict[str, Any], source_type: str, sinks: list[str]
) -> None:
    if source_type not in sources:
        raise HTTPException(422, f"Unknown source_type: {source_type!r}")
    unknown_sinks = [s for s in sinks if s not in sinks_registry]
    if unknown_sinks:
        raise HTTPException(422, f"Unknown sink(s): {', '.join(unknown_sinks)}")
