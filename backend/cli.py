"""Phase 1 entry point (spec §4.2): run the pipeline without API or UI.

    python cli.py ingest --source obsidian --path ./fixtures/vault \\
        --sink neo4j --config default.yml --db ./loom.sqlite3

    python cli.py ingest --source docx --path ./fixtures/docs \\
        --sink neo4j --config br_requirements.yml --db ./loom.sqlite3

`--db` is optional: omitting it runs a one-shot full ingest with no
hash-skip, no doc-removal cleanup, and no SQLite side effects (the same
shape a `preview` run wants — see `Pipeline.run`'s docstring). Passing it
enables incremental re-ingestion (spec §6.1-§6.3) against that SQLite file
(created if missing; `:memory:` also works, though it's only useful for
in-process callers since the CLI process doesn't outlive the command).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Callable

from app.jobs.store import HashStore, connect
from app.pipeline.core import Pipeline
from app.pipeline.registry import SINKS, SOURCES
from app.pipeline.sinks.base import SinkAdapter
from app.pipeline.sources.base import SourceAdapter


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="cli.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Run the pipeline once against a source")
    ingest.add_argument("--source", required=True, choices=sorted(SOURCES))
    ingest.add_argument("--path", required=True, help="Path to the vault/document folder")
    ingest.add_argument("--sink", required=True, nargs="+", choices=sorted(SINKS))
    ingest.add_argument("--config", required=True, help="Path to the source's YAML config")
    ingest.add_argument(
        "--db",
        default=None,
        help=(
            "Path to the SQLite operational store (spec §6.1). Enables "
            "incremental re-ingestion: unchanged docs are skipped, changed "
            "docs are cleaned up before rewriting, and docs removed from "
            "the source are cleaned up too. Omit for a one-shot full ingest."
        ),
    )

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

    store = HashStore(connect(args.db)) if args.db else None

    def progress(doc_id: str, fraction: float) -> None:
        print(f"[{fraction:5.0%}] {doc_id}")

    result = Pipeline().run(
        source=source,
        source_path=args.path,
        sinks=active_sinks,
        config=config,
        progress=progress,
        store=store,
    )

    failed = [s for s in result.doc_statuses if s.outcome == "failed"]
    updated = sum(1 for s in result.doc_statuses if s.outcome == "updated")
    skipped = sum(1 for s in result.doc_statuses if s.outcome == "skipped")
    removed = sum(1 for s in result.doc_statuses if s.outcome == "removed")
    print(
        f"Done: {updated} updated, {skipped} skipped, {removed} removed, "
        f"{len(failed)} failed, {len(result.doc_statuses)} total"
    )
    for status in failed:
        print(f"  FAILED {status.doc_id}: {status.error}")
    for orphan in result.orphans:
        print(f"  ORPHANED {orphan.edge_id}: {orphan.reason}")

    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "ingest":
        return run_ingest(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
