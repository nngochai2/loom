"""End-to-end exercise of the fixture vault through the real pipeline
(discover -> load -> extract -> write), using a recording fake sink in
place of Neo4j (no live Neo4j available in this environment — see
docker-compose.yml at the repo root for real integration testing).
"""

from pathlib import Path

from app.pipeline.core import Pipeline
from app.pipeline.sources.obsidian import ObsidianSourceAdapter, load_config
from app.pipeline.types import ExtractionResult, SinkReport

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VAULT = FIXTURES / "vault"
CONFIG_PATH = FIXTURES / "obsidian_config.yml"


class RecordingSink:
    sink_type = "recording"

    def __init__(self) -> None:
        self.results: dict[str, ExtractionResult] = {}

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport:
        self.results[doc_id] = result
        return SinkReport(sink_type=self.sink_type)

    def delete_non_curated_for_doc(self, doc_id: str) -> int:
        return 0


def _run():
    config = load_config(str(CONFIG_PATH))
    source = ObsidianSourceAdapter(config)
    sink = RecordingSink()
    job_result = Pipeline().run(
        source=source,
        source_path=str(VAULT),
        sinks=[sink],
        config=config,
        progress=lambda doc_id, fraction: None,
    )
    return job_result, sink


def _entity_by_name(sink: RecordingSink, name: str):
    for result in sink.results.values():
        for entity in result.entities:
            if entity.name == name:
                return entity
    return None


def test_fixture_vault_has_at_least_eight_notes_all_updated():
    job_result, _sink = _run()
    assert len(job_result.doc_statuses) >= 8
    assert all(s.outcome == "updated" for s in job_result.doc_statuses)


def test_subfolder_classification_matches_expected_types():
    _job_result, sink = _run()
    assert _entity_by_name(sink, "Auth Service").type == "ARCHITECTURE"
    assert _entity_by_name(sink, "Fix Login Bug").type == "TASK"
    assert _entity_by_name(sink, "Token Format Convention").type == "CONVENTION"
    assert _entity_by_name(sink, "Session Glossary Term").type == "BUSINESS_TERM"


def test_note_outside_subfolder_map_falls_back_to_keyword_classification():
    _job_result, sink = _run()
    entity = _entity_by_name(sink, "Standup Notes")
    assert entity.type == "TASK"  # "ticket" keyword signal
    assert entity.rule_id == "keyword-signal:TASK"


def test_tag_folder_notes_become_tag_entities():
    _job_result, sink = _run()
    backend_tag = _entity_by_name(sink, "backend")
    urgent_tag = _entity_by_name(sink, "urgent")
    assert backend_tag.type == "TAG"
    assert urgent_tag.type == "TAG"


def test_wikilinks_resolve_and_dangling_link_is_dropped():
    _job_result, sink = _run()
    fix_login_result = next(
        r for r in sink.results.values() if any(e.name == "Fix Login Bug" for e in r.entities)
    )
    # Body has 3 wikilinks: Auth Service (resolves), API Gateway (resolves),
    # Ghost Note (dangling - dropped).
    assert len(fix_login_result.relationships) == 2
    assert all(rel.origin == "explicit" for rel in fix_login_result.relationships)
    assert all(rel.rule_id is None for rel in fix_login_result.relationships)


def test_every_written_element_carries_mandatory_properties():
    _job_result, sink = _run()
    for result in sink.results.values():
        assert result.content_hash
        for entity in result.entities:
            assert entity.origin == "extracted"
            assert entity.rule_id is not None
        for rel in result.relationships:
            assert rel.origin == "explicit"
            assert rel.rule_id is None  # absent on explicit, per spec §5
