"""Integration tests for `DocxSourceAdapter.extract()`'s opt-in LLM
prose-extraction path (ADR-0018, issue #17): wiring the two mechanisms
(regex over table rows, LLM over prose content) together into one
`ExtractionResult`. Mocks `ollama_client.generate` — real-model recall is
issue #18's own fixture gate, not this test's job.
"""

import dataclasses
import json
from pathlib import Path

from app.llm import ollama_client
from app.pipeline.rules.schema import ProseExtraction, RuleContext, load_rule_file
from app.pipeline.sources.docx import DocxSourceAdapter
from tests.conftest import mock_ollama_generate

FIXTURES_DIR = Path(__file__).parent / "fixtures"
DOCS_DIR = FIXTURES_DIR / "docs"
RULE_PATH = FIXTURES_DIR / "br_requirements.yml"

PROSE_EXTRACTION = ProseExtraction(
    enabled=True,
    id="pe-intro",
    target_entity_types=("TASK", "BUSINESS_TERM"),
    target_relationship_types=("RELATES_TO",),
)


def _adapter_with_prose_extraction(prose_extraction: ProseExtraction) -> DocxSourceAdapter:
    rule_file = dataclasses.replace(
        load_rule_file(str(RULE_PATH)),
        context=RuleContext(
            include_paragraphs=True,
            include_non_br_tables=True,
            prose_extraction=prose_extraction,
        ),
    )
    return DocxSourceAdapter(rule_file)


def _doc_for(docs, filename: str):
    return next(d for d in docs if d.path.endswith(filename))


_mock_generate = mock_ollama_generate


def test_prose_extraction_disabled_by_default_makes_no_llm_call(monkeypatch):
    calls = _mock_generate(monkeypatch, "{}")
    adapter = _adapter_with_prose_extraction(ProseExtraction())  # enabled=False

    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "plain_prose.docx")
    result = adapter.extract(adapter.load(doc), adapter.rule_file)

    assert calls == []
    assert result.entities == ()
    assert result.relationships == ()


def test_prose_extraction_success_leaves_the_result_warning_none(monkeypatch):
    _mock_generate(monkeypatch, json.dumps({"entities": [], "relationships": []}))
    adapter = _adapter_with_prose_extraction(PROSE_EXTRACTION)

    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "plain_prose.docx")
    result = adapter.extract(adapter.load(doc), adapter.rule_file)

    assert result.warning is None


def test_prose_extraction_enabled_produces_extracted_entities_from_prose(monkeypatch):
    response = json.dumps(
        {
            "entities": [
                {"type": "TASK", "name": "Read the architecture overview"},
                {"type": "TASK", "name": "Pair with a mentor"},
            ],
            "relationships": [
                {
                    "type": "RELATES_TO",
                    "from": "Read the architecture overview",
                    "to": "Pair with a mentor",
                }
            ],
        }
    )
    _mock_generate(monkeypatch, response)
    adapter = _adapter_with_prose_extraction(PROSE_EXTRACTION)

    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "plain_prose.docx")
    result = adapter.extract(adapter.load(doc), adapter.rule_file)

    assert len(result.entities) == 2
    assert {e.name for e in result.entities} == {
        "Read the architecture overview",
        "Pair with a mentor",
    }
    assert all(e.origin == "extracted" for e in result.entities)
    assert all(e.rule_id == "pe-intro" for e in result.entities)

    assert len(result.relationships) == 1
    assert result.relationships[0].rule_id == "pe-intro"


def test_prose_extraction_merges_alongside_regex_output_without_disturbing_it(monkeypatch):
    response = json.dumps(
        {
            "entities": [{"type": "BUSINESS_TERM", "name": "Discovery workshop"}],
            "relationships": [],
        }
    )
    _mock_generate(monkeypatch, response)
    adapter = _adapter_with_prose_extraction(PROSE_EXTRACTION)

    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "with_tables.docx")
    result = adapter.extract(adapter.load(doc), adapter.rule_file)

    regex_entities = [e for e in result.entities if e.rule_id == "id-pattern-match"]
    prose_entities = [e for e in result.entities if e.rule_id == "pe-intro"]

    assert {e.properties["req_id"] for e in regex_entities} == {"BR01", "BR02"}
    assert [e.name for e in prose_entities] == ["Discovery workshop"]
    assert len(result.entities) == len(regex_entities) + len(prose_entities)


def test_prose_extraction_skips_llm_call_when_no_prose_content_collected(monkeypatch):
    calls = _mock_generate(monkeypatch, "{}")
    rule_file = dataclasses.replace(
        load_rule_file(str(RULE_PATH)),
        context=RuleContext(
            include_paragraphs=False,
            include_non_br_tables=False,
            prose_extraction=PROSE_EXTRACTION,
        ),
    )
    adapter = DocxSourceAdapter(rule_file)

    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "zero_extraction.docx")
    loaded = adapter.load(doc)
    assert loaded.content == ""

    result = adapter.extract(loaded, adapter.rule_file)

    assert calls == []
    assert result.entities == ()


# --- Partial success on prose-extraction failure (ADR-0022, issue #20):
# Ollama unreachable/timing out/unusable degrades this doc, it doesn't
# fail it -- regex-derived output still comes back, with a warning. ---


def _mock_generate_failure(monkeypatch, exc: Exception) -> None:
    def fake_generate(prompt: str, *, client=None) -> str:
        raise exc

    monkeypatch.setattr(ollama_client, "generate", fake_generate)


def test_prose_extraction_failure_still_returns_regex_derived_entities(monkeypatch):
    _mock_generate_failure(monkeypatch, ollama_client.OllamaError("connection refused"))
    adapter = _adapter_with_prose_extraction(PROSE_EXTRACTION)

    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "with_tables.docx")
    result = adapter.extract(adapter.load(doc), adapter.rule_file)

    # No prose items (the LLM call never succeeded), but the regex-derived
    # requirements from the same doc's table rows are entirely unaffected.
    assert {e.properties["req_id"] for e in result.entities} == {"BR01", "BR02"}
    assert all(e.rule_id == "id-pattern-match" for e in result.entities)


def test_prose_extraction_failure_surfaces_a_warning_naming_the_prose_rule(monkeypatch):
    _mock_generate_failure(monkeypatch, ollama_client.OllamaError("connection refused"))
    adapter = _adapter_with_prose_extraction(PROSE_EXTRACTION)

    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "with_tables.docx")
    result = adapter.extract(adapter.load(doc), adapter.rule_file)

    assert result.warning is not None
    assert "pe-intro" in result.warning
    assert "connection refused" in result.warning


def test_prose_extraction_failure_on_a_doc_with_no_regex_matches_still_writes_nothing_but_a_warning(
    monkeypatch,
):
    _mock_generate_failure(monkeypatch, ollama_client.OllamaError("timed out"))
    adapter = _adapter_with_prose_extraction(PROSE_EXTRACTION)

    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "plain_prose.docx")
    result = adapter.extract(adapter.load(doc), adapter.rule_file)

    assert result.entities == ()
    assert result.relationships == ()
    assert result.warning is not None
