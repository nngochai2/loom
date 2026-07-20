"""Phase 1 entry point (spec §4.2): run the pipeline without API or UI.

    python cli.py ingest --source obsidian --path ./fixtures/vault \\
        --sink neo4j --config default.yml
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Callable

from app.pipeline.core import Pipeline
from app.pipeline.sinks.base import SinkAdapter
from app.pipeline.sinks.neo4j import Neo4jSink
from app.pipeline.sources.base import SourceAdapter
from app.pipeline.sources.obsidian import ObsidianSourceAdapter
from app.pipeline.sources.obsidian import load_config as load_obsidian_config

# One entry per source_type; docx registers here too once its ticket lands.
SOURCES: dict[str, tuple[type, Callable[[str], Any]]] = {
    "obsidian": (ObsidianSourceAdapter, load_obsidian_config),
}

# One entry per sink_type; chroma registers here once its ticket lands.
SINKS: dict[str, Callable[[], SinkAdapter]] = {
    "neo4j": Neo4jSink,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="cli.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Run the pipeline once against a source")
    ingest.add_argument("--source", required=True, choices=sorted(SOURCES))
    ingest.add_argument("--path", required=True, help="Path to the vault/document folder")
    ingest.add_argument("--sink", required=True, nargs="+", choices=sorted(SINKS))
    ingest.add_argument("--config", required=True, help="Path to the source's YAML config")

    return parser.parse_args(argv)


def run_ingest(
    args: argparse.Namespace,
    sources: dict[str, tuple[type, Callable[[str], Any]]] = SOURCES,
    sinks: dict[str, Callable[[], SinkAdapter]] = SINKS,
) -> int:
    adapter_cls, config_loader = sources[args.source]
    config = config_loader(args.config)
    source: SourceAdapter = adapter_cls(config)
    active_sinks = [sinks[name]() for name in args.sink]

    def progress(doc_id: str, fraction: float) -> None:
        print(f"[{fraction:5.0%}] {doc_id}")

    result = Pipeline().run(
        source=source,
        source_path=args.path,
        sinks=active_sinks,
        config=config,
        progress=progress,
    )

    failed = [s for s in result.doc_statuses if s.outcome == "failed"]
    updated = sum(1 for s in result.doc_statuses if s.outcome == "updated")
    print(f"Done: {updated} updated, {len(failed)} failed, {len(result.doc_statuses)} total")
    for status in failed:
        print(f"  FAILED {status.doc_id}: {status.error}")

    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "ingest":
        return run_ingest(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
