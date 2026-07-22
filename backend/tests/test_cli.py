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
from app.llm import ollama_client
from app.pipeline.types import DeleteReport, ExtractionResult, SinkReport


class RecordingSink:
    sink_type = "recording"

    def __init__(self) -> None:
        self.writes: list[tuple[str, ExtractionResult]] = []
        self.deletes: list[str] = []

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport:
        self.writes.append((doc_id, result))
        return SinkReport(sink_type=self.sink_type)

    def delete_non_curated_for_doc(self, doc_id: str) -> DeleteReport:
        self.deletes.append(doc_id)
        return DeleteReport(deleted_count=0)


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


def test_parse_args_db_defaults_to_none_for_a_one_shot_full_ingest():
    args = cli.parse_args(
        ["ingest", "--source", "obsidian", "--path", ".", "--sink", "neo4j", "--config", "x.yml"]
    )

    assert args.db is None


# --- --db wiring: incremental re-ingestion (spec §6.1) end-to-end through the CLI ---


def test_run_ingest_with_db_reports_all_skipped_and_writes_nothing_on_an_unchanged_rerun(tmp_path):
    vault = _write_vault(tmp_path)
    config_path = _write_config(tmp_path)
    db_path = tmp_path / "loom.sqlite3"

    def _ingest(sink: RecordingSink) -> int:
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
                "--db",
                str(db_path),
            ]
        )
        return cli.run_ingest(args, sinks={"neo4j": lambda: sink})

    first_sink = RecordingSink()
    assert _ingest(first_sink) == 0
    assert len(first_sink.writes) == 2

    second_sink = RecordingSink()
    assert _ingest(second_sink) == 0
    assert second_sink.writes == []
    assert second_sink.deletes == []


def _write_prose_extraction_config(tmp_path):
    config_path = tmp_path / "prose_config.yml"
    config_path.write_text(
        """\
name: "BR with prose extraction"
node_label: REQUIREMENT
id_pattern: '^BR\\s*(\\d+)$'
id_flags: IGNORECASE
id_format: 'BR{:02d}'
context:
  include_paragraphs: true
  include_non_br_tables: true
  prose_extraction:
    enabled: true
    id: pe-cli-test
    target_entity_types: [TASK]
""",
        encoding="utf-8",
    )
    return config_path


def test_run_ingest_reruns_prose_extraction_when_the_configured_model_changes(
    tmp_path, monkeypatch
):
    # ADR-0020/issue #19 end-to-end through the CLI: unchanged docx content
    # plus an unchanged model/prompt_version is skipped like any other
    # unchanged doc; a model swap alone (content still unchanged) forces
    # every doc using prose extraction to be reprocessed anyway.
    monkeypatch.setattr(
        ollama_client, "generate", lambda prompt, *, client=None: '{"entities": [], "relationships": []}'
    )
    fixtures_dir = Path(__file__).parent / "fixtures"
    config_path = _write_prose_extraction_config(tmp_path)
    db_path = tmp_path / "loom.sqlite3"

    def _ingest(sink: RecordingSink) -> int:
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
                str(config_path),
                "--db",
                str(db_path),
            ]
        )
        return cli.run_ingest(args, sinks={"neo4j": lambda: sink})

    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")
    first_sink = RecordingSink()
    assert _ingest(first_sink) == 0
    assert len(first_sink.writes) == 3

    same_model_sink = RecordingSink()
    assert _ingest(same_model_sink) == 0
    assert same_model_sink.writes == []  # unchanged content + unchanged model: skipped

    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2")
    new_model_sink = RecordingSink()
    assert _ingest(new_model_sink) == 0
    assert len(new_model_sink.writes) == 3  # content unchanged, but model changed
    assert len(new_model_sink.deletes) == 3  # prior (non-curated) contributions cleared first


def test_run_ingest_without_db_does_not_persist_a_hash_table(tmp_path):
    # Omitting --db must not create incremental state anywhere the next
    # command-line run (with or without --db) could pick up by accident.
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
    cli.run_ingest(args, sinks={"neo4j": lambda: sink})

    assert not (tmp_path / "loom.sqlite3").exists()
