"""cli.py's ingest command wires a real vault through the real pipeline.
The Neo4j sink itself can't be exercised end-to-end here (no live Neo4j in
this environment), so these tests inject a recording fake sink via the
`sinks` registry parameter — the same seam production code leaves at its
default (the real `Neo4jSink`).
"""

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import cli
from app.pipeline.types import ExtractionResult, SinkReport


class RecordingSink:
    sink_type = "recording"

    def __init__(self) -> None:
        self.writes: list[tuple[str, ExtractionResult]] = []
        self.deletes: list[str] = []

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport:
        self.writes.append((doc_id, result))
        return SinkReport(sink_type=self.sink_type)

    def delete_non_curated_for_doc(self, doc_id: str) -> int:
        self.deletes.append(doc_id)
        return 0


def _write_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Project" / "Architecture").mkdir(parents=True)
    (vault / "Project" / "Architecture" / "Auth Service.md").write_text(
        "This service depends on [[API Gateway]].\n", encoding="utf-8"
    )
    (vault / "Project" / "Architecture" / "API Gateway.md").write_text(
        "The gateway.\n", encoding="utf-8"
    )
    return vault


def _write_config(tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """\
include_folders:
  - "Project"
tags_folder: "Tags"
main_folder: "Project"
subfolder_type_map:
  architecture: ARCHITECTURE
type_signals: {}
rel_keywords:
  "depends on": DEPENDS_ON
""",
        encoding="utf-8",
    )
    return config_path


def test_run_ingest_processes_every_discovered_doc_via_registered_sink(tmp_path):
    vault = _write_vault(tmp_path)
    config_path = _write_config(tmp_path)
    sink = RecordingSink()

    args = cli.parse_args(
        [
            "ingest",
            "--source",
            "obsidian",
            "--path",
            str(vault),
            "--sink",
            "neo4j",
            "--config",
            str(config_path),
        ]
    )

    exit_code = cli.run_ingest(args, sinks={"neo4j": lambda: sink})

    assert exit_code == 0
    assert {doc_id for doc_id, _ in sink.writes} == {
        doc_id for doc_id, _ in sink.writes
    }  # sanity: no crash
    assert len(sink.writes) == 2


def test_run_ingest_returns_nonzero_exit_code_on_failed_docs(tmp_path, monkeypatch):
    vault = _write_vault(tmp_path)
    config_path = _write_config(tmp_path)
    sink = RecordingSink()

    args = cli.parse_args(
        [
            "ingest",
            "--source",
            "obsidian",
            "--path",
            str(vault),
            "--sink",
            "neo4j",
            "--config",
            str(config_path),
        ]
    )

    # Force every doc to fail by breaking the source's load() after discovery.
    from app.pipeline.sources.obsidian import ObsidianSourceAdapter

    def broken_load(self, doc):
        raise ValueError("boom")

    monkeypatch.setattr(ObsidianSourceAdapter, "load", broken_load)

    exit_code = cli.run_ingest(args, sinks={"neo4j": lambda: sink})

    assert exit_code == 1
    assert sink.writes == []


def test_run_ingest_processes_docx_fixtures_via_registered_sink():
    fixtures_dir = Path(__file__).parent / "fixtures"
    sink = RecordingSink()

    args = cli.parse_args(
        [
            "ingest",
            "--source",
            "docx",
            "--path",
            str(fixtures_dir / "docs"),
            "--sink",
            "neo4j",
            "--config",
            str(fixtures_dir / "br_requirements.yml"),
        ]
    )

    exit_code = cli.run_ingest(args, sinks={"neo4j": lambda: sink})

    assert exit_code == 0
    assert len(sink.writes) == 3  # plain_prose, with_tables, zero_extraction
    entity_counts = {doc_id: len(result.entities) for doc_id, result in sink.writes}
    assert sorted(entity_counts.values()) == [0, 0, 2]


def test_parse_args_rejects_unknown_source():
    import pytest

    with pytest.raises(SystemExit):
        cli.parse_args(
            ["ingest", "--source", "xlsx", "--path", ".", "--sink", "neo4j", "--config", "x.yml"]
        )
