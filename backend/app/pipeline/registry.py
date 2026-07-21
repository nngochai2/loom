"""Source/sink registries (spec §4.2), shared by `cli.py` and the Jobs API
runner (`app/jobs/runner.py`) so both entry points dispatch `source_type`/
`sink_type` strings through the exact same mapping — one registry, not two
that could drift.
"""

from __future__ import annotations

from typing import Any, Callable

from app.pipeline.rules.schema import load_rule_file, rule_file_from_dict
from app.pipeline.sinks.base import SinkAdapter
from app.pipeline.sinks.dryrun import DryRunSink
from app.pipeline.sinks.neo4j import Neo4jSink
from app.pipeline.sources.docx import DocxSourceAdapter
from app.pipeline.sources.obsidian import ObsidianSourceAdapter
from app.pipeline.sources.obsidian import load_config as load_obsidian_config
from app.pipeline.sources.obsidian import obsidian_config_from_dict

# One entry per source_type.
SOURCES: dict[str, tuple[type, Callable[[str], Any]]] = {
    "obsidian": (ObsidianSourceAdapter, load_obsidian_config),
    "docx": (DocxSourceAdapter, load_rule_file),
}

# The dict-input counterpart to SOURCES' path-input config loaders -- same
# adapter, same construction, just fed from ConfigsStore's already-parsed
# YAML instead of a path (`app/api/preview.py`, ticket #9).
CONFIG_FROM_DICT: dict[str, Callable[[dict[str, Any]], Any]] = {
    "obsidian": obsidian_config_from_dict,
    "docx": rule_file_from_dict,
}

# One entry per sink_type; chroma registers here once its ticket lands.
SINKS: dict[str, Callable[[], SinkAdapter]] = {
    "neo4j": Neo4jSink,
    "dryrun": DryRunSink,
}
