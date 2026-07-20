"""End-to-end exercise of the fixture docx set through the real pipeline
(discover -> load -> extract -> write), using a recording fake sink in
place of Neo4j (no live Neo4j available in this environment — see
docker-compose.yml at the repo root for real integration testing).

Fixture set (spec §11): one plain-prose docx, one with a requirements
table, one that intentionally triggers zero extractions.
"""

from pathlib import Path

from app.pipeline.core import Pipeline
from app.pipeline.rules.schema import load_rule_file
from app.pipeline.sources.docx import DocxSourceAdapter
from app.pipeline.types import DeleteReport, ExtractionResult, SinkReport

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DOCS_DIR = FIXTURES / "docs"
RULE_PATH = FIXTURES / "br_requirements.yml"


class RecordingSink:
    sink_type = "recording"

    def __init__(self) -> None:
        self.results: dict[str, ExtractionResult] = {}

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport:
        self.results[doc_id] = result
        return SinkReport(sink_type=self.sink_type)

    def delete_non_curated_for_doc(self, doc_id: str) -> DeleteReport:
        return DeleteReport(deleted_count=0)


def _run():
    rule_file = load_rule_file(str(RULE_PATH))
    source = DocxSourceAdapter(rule_file)
    sink = RecordingSink()
    job_result = Pipeline().run(
        source=source,
        source_path=str(DOCS_DIR),
        sinks=[sink],
        config=rule_file,
        progress=lambda doc_id, fraction: None,
    )
    return job_result, sink


def test_fixture_docs_all_three_files_ingest_as_updated():
    job_result, _sink = _run()
    assert len(job_result.doc_statuses) == 3
    assert all(s.outcome == "updated" for s in job_result.doc_statuses)


def test_only_the_table_fixture_produces_requirement_entities():
    _job_result, sink = _run()
    entity_counts = sorted(len(result.entities) for result in sink.results.values())
    assert entity_counts == [0, 0, 2]


def test_every_written_entity_carries_mandatory_properties():
    _job_result, sink = _run()
    for result in sink.results.values():
        assert result.content_hash
        for entity in result.entities:
            assert entity.type == "REQUIREMENT"
            assert entity.origin == "extracted"
            assert entity.rule_id == "id-pattern-match"


def test_no_relationships_are_produced_without_a_parent_node_id():
    # This adapter never supplies parent_node_id (ADR-0006: no
    # document-hierarchy concept in Loom's core), so the generic
    # parent-link mechanism stays dormant for this fixture set.
    _job_result, sink = _run()
    for result in sink.results.values():
        assert result.relationships == ()
