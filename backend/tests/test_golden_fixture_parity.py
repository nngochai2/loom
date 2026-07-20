"""The Phase 1 exit gate (spec §10, ADR-0007): proves Loom's ported source
adapters preserve NAA's actual extraction behavior on the shared fixture
set, rather than silently drifting from it.

This test never imports or executes anything from NAA (a machine-local
sibling repo at `D:\\Cloned Projects\\NAA` that doesn't exist on a CI
runner) -- it only loads the golden snapshot JSON files committed under
`tests/fixtures/golden/`, which were produced by running NAA's *real*
current parsers once via `backend/scripts/generate_golden_fixture_snapshot.py`
(see that script's docstring for how/when to regenerate them). That keeps
this file 100% CI-safe while still gating on NAA's actual behavior.
"""

import json
from pathlib import Path

from golden_fixture_normalize import (
    diff_records,
    docx_entity_key,
    edge_key,
    entity_key,
    normalize_loom_docx_entity,
    normalize_loom_obsidian_edge,
    normalize_loom_obsidian_entity,
)

from app.pipeline.core import Pipeline
from app.pipeline.rules.schema import load_rule_file
from app.pipeline.sources.docx import DocxSourceAdapter
from app.pipeline.sources.obsidian import ObsidianSourceAdapter, load_config
from app.pipeline.types import ExtractionResult, SinkReport

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES / "golden"
VAULT = FIXTURES / "vault"
OBSIDIAN_CONFIG_PATH = FIXTURES / "obsidian_config.yml"
DOCS_DIR = FIXTURES / "docs"
DOCX_RULE_PATH = FIXTURES / "br_requirements.yml"


class _RecordingSink:
    """Same recording fake used by the other fixture integration tests
    (test_fixture_vault_integration.py, test_fixture_docs_integration.py)
    -- no live Neo4j needed to exercise discover -> load -> extract."""

    sink_type = "recording"

    def __init__(self) -> None:
        self.results: dict[str, ExtractionResult] = {}

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport:
        self.results[doc_id] = result
        return SinkReport(sink_type=self.sink_type)

    def delete_non_curated_for_doc(self, doc_id: str) -> int:
        return 0


def _run_loom_obsidian():
    config = load_config(str(OBSIDIAN_CONFIG_PATH))
    source = ObsidianSourceAdapter(config)
    sink = _RecordingSink()
    Pipeline().run(
        source=source,
        source_path=str(VAULT),
        sinks=[sink],
        config=config,
        progress=lambda doc_id, fraction: None,
    )
    return sink


def _run_loom_docx():
    rule_file = load_rule_file(str(DOCX_RULE_PATH))
    source = DocxSourceAdapter(rule_file)
    sink = _RecordingSink()
    Pipeline().run(
        source=source,
        source_path=str(DOCS_DIR),
        sinks=[sink],
        config=rule_file,
        progress=lambda doc_id, fraction: None,
    )
    return sink, source


def test_obsidian_adapter_matches_naas_golden_snapshot():
    golden = json.loads((GOLDEN / "obsidian_vault.json").read_text(encoding="utf-8"))
    sink = _run_loom_obsidian()

    actual_entities = [
        normalize_loom_obsidian_entity(entity) for result in sink.results.values() for entity in result.entities
    ]
    actual_edges = [
        normalize_loom_obsidian_edge(rel) for result in sink.results.values() for rel in result.relationships
    ]

    problems = diff_records(golden["entities"], actual_entities, entity_key, "obsidian entity")
    problems += diff_records(golden["edges"], actual_edges, edge_key, "obsidian edge")

    assert not problems, "\n".join(problems)


def test_obsidian_golden_snapshot_is_non_trivial():
    # Guards against a vacuously-passing gate (e.g. an empty/corrupted
    # snapshot that would make the diff above trivially empty).
    golden = json.loads((GOLDEN / "obsidian_vault.json").read_text(encoding="utf-8"))
    assert len(golden["entities"]) >= 8
    assert len(golden["edges"]) > 0


def test_docx_adapter_matches_naas_golden_snapshot():
    golden = json.loads((GOLDEN / "docx_fixtures.json").read_text(encoding="utf-8"))
    sink, source = _run_loom_docx()
    docs_by_id = {d.doc_id: Path(d.path).name for d in source.discover(str(DOCS_DIR))}

    problems: list[str] = []
    for doc_id, result in sink.results.items():
        filename = docs_by_id[doc_id]
        actual_entities = [normalize_loom_docx_entity(e) for e in result.entities]
        problems += diff_records(
            golden[filename]["entities"], actual_entities, docx_entity_key, f"docx entity ({filename})"
        )

    assert not problems, "\n".join(problems)


def test_docx_golden_snapshot_covers_every_fixture_file():
    golden = json.loads((GOLDEN / "docx_fixtures.json").read_text(encoding="utf-8"))
    assert set(golden.keys()) == {"plain_prose.docx", "with_tables.docx", "zero_extraction.docx"}
    # Matches the checked-in fixture set's known shape (spec §11): only
    # the table fixture produces requirement entities.
    assert golden["plain_prose.docx"]["entities"] == []
    assert golden["zero_extraction.docx"]["entities"] == []
    assert len(golden["with_tables.docx"]["entities"]) == 2
