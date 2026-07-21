"""Source/sink registries (spec §4.2), shared by `cli.py` and the Jobs API
runner (`app/jobs/runner.py`) so both entry points dispatch `source_type`/
`sink_type` strings through the exact same mapping — one registry, not two
that could drift.
"""

from __future__ import annotations

from typing import Any, Callable

from app.pipeline.sinks.base import SinkAdapter
from app.pipeline.sinks.neo4j import Neo4jSink
from app.pipeline.sources.docx import DocxSourceAdapter
from app.pipeline.sources.docx import load_config as load_docx_config
from app.pipeline.sources.obsidian import ObsidianSourceAdapter
from app.pipeline.sources.obsidian import load_config as load_obsidian_config

# One entry per source_type.
SOURCES: dict[str, tuple[type, Callable[[str], Any]]] = {
    "obsidian": (ObsidianSourceAdapter, load_obsidian_config),
    "docx": (DocxSourceAdapter, load_docx_config),
}

# One entry per sink_type; chroma/dryrun register here once their tickets land.
SINKS: dict[str, Callable[[], SinkAdapter]] = {
    "neo4j": Neo4jSink,
}
