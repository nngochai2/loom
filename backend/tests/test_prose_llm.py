"""Tests for the LLM prose-extraction path (ADR-0018, issue #17). Mocks
`app.llm.ollama_client.generate` throughout — no real Ollama call — since
this module only owns prompt-building/response-parsing/entity-shaping;
recall-quality against a real model is issue #18's own fixture gate.
"""

import json

import pytest

from app.llm import ollama_client
from app.pipeline.extraction.prose_llm import ProseExtractionError, extract_prose_entities
from app.pipeline.rules.schema import ProseExtraction
from tests.conftest import mock_ollama_generate

PROSE = ProseExtraction(
    enabled=True,
    id="pe-intro",
    target_entity_types=("TASK", "BUSINESS_TERM"),
    target_relationship_types=("RELATES_TO",),
)

_mock_generate = mock_ollama_generate


def test_extract_prose_entities_returns_entities_and_relationships(monkeypatch):
    response = json.dumps(
        {
            "entities": [
                {"type": "TASK", "name": "Pair with a mentor"},
                {"type": "BUSINESS_TERM", "name": "Onboarding"},
            ],
            "relationships": [
                {"type": "RELATES_TO", "from": "Pair with a mentor", "to": "Onboarding"},
            ],
        }
    )
    _mock_generate(monkeypatch, response)

    entities, relationships = extract_prose_entities(
        "New hires should pair with a mentor as part of onboarding.",
        PROSE,
        doc_id="doc-1",
        source_file="onboarding.docx",
    )

    assert len(entities) == 2
    assert {e.name for e in entities} == {"Pair with a mentor", "Onboarding"}
    assert all(e.origin == "extracted" for e in entities)
    assert all(e.rule_id == "pe-intro" for e in entities)
    assert all(e.properties["source_file"] == "onboarding.docx" for e in entities)

    assert len(relationships) == 1
    rel = relationships[0]
    assert rel.type == "RELATES_TO"
    assert rel.origin == "extracted"
    assert rel.rule_id == "pe-intro"
    task = next(e for e in entities if e.name == "Pair with a mentor")
    term = next(e for e in entities if e.name == "Onboarding")
    assert rel.from_id == task.id
    assert rel.to_id == term.id


def test_extract_prose_entities_skips_llm_call_for_blank_content(monkeypatch):
    prompts = _mock_generate(monkeypatch, "{}")

    entities, relationships = extract_prose_entities(
        "   \n  ", PROSE, doc_id="doc-1", source_file="f.docx"
    )

    assert entities == ()
    assert relationships == ()
    assert prompts == []


def test_extract_prose_entities_filters_out_entity_types_not_in_target_list(monkeypatch):
    response = json.dumps(
        {
            "entities": [
                {"type": "TASK", "name": "Allowed task"},
                {"type": "ARCHITECTURE", "name": "Not requested"},
            ],
            "relationships": [],
        }
    )
    _mock_generate(monkeypatch, response)

    entities, _ = extract_prose_entities(
        "some prose", PROSE, doc_id="doc-1", source_file="f.docx"
    )

    assert [e.name for e in entities] == ["Allowed task"]


def test_extract_prose_entities_filters_out_relationship_types_not_in_target_list(monkeypatch):
    response = json.dumps(
        {
            "entities": [
                {"type": "TASK", "name": "A"},
                {"type": "TASK", "name": "B"},
            ],
            "relationships": [
                {"type": "DEPENDS_ON", "from": "A", "to": "B"},
            ],
        }
    )
    _mock_generate(monkeypatch, response)

    _, relationships = extract_prose_entities(
        "some prose", PROSE, doc_id="doc-1", source_file="f.docx"
    )

    assert relationships == ()


def test_extract_prose_entities_drops_relationships_referencing_unknown_entity_names(monkeypatch):
    response = json.dumps(
        {
            "entities": [{"type": "TASK", "name": "A"}],
            "relationships": [
                {"type": "RELATES_TO", "from": "A", "to": "Ghost"},
            ],
        }
    )
    _mock_generate(monkeypatch, response)

    _, relationships = extract_prose_entities(
        "some prose", PROSE, doc_id="doc-1", source_file="f.docx"
    )

    assert relationships == ()


def test_extract_prose_entities_parses_json_wrapped_in_markdown_fences(monkeypatch):
    response = "Sure, here you go:\n```json\n" + json.dumps(
        {"entities": [{"type": "TASK", "name": "A"}], "relationships": []}
    ) + "\n```"
    _mock_generate(monkeypatch, response)

    entities, _ = extract_prose_entities(
        "some prose", PROSE, doc_id="doc-1", source_file="f.docx"
    )

    assert [e.name for e in entities] == ["A"]


def test_extract_prose_entities_raises_prose_extraction_error_on_unparsable_response(monkeypatch):
    _mock_generate(monkeypatch, "not json at all")

    with pytest.raises(ProseExtractionError, match="no JSON object found"):
        extract_prose_entities("some prose", PROSE, doc_id="doc-1", source_file="f.docx")


def test_extract_prose_entities_raises_prose_extraction_error_on_ollama_failure(monkeypatch):
    def fake_generate(prompt: str, *, client=None) -> str:
        raise ollama_client.OllamaError("connection refused")

    monkeypatch.setattr(ollama_client, "generate", fake_generate)

    with pytest.raises(ProseExtractionError, match="connection refused"):
        extract_prose_entities("some prose", PROSE, doc_id="doc-1", source_file="f.docx")


def test_extract_prose_entities_scopes_node_id_to_doc_and_type(monkeypatch):
    response = json.dumps(
        {"entities": [{"type": "TASK", "name": "Same name"}], "relationships": []}
    )
    _mock_generate(monkeypatch, response)

    entities_doc1, _ = extract_prose_entities(
        "x", PROSE, doc_id="doc-1", source_file="f.docx"
    )
    entities_doc2, _ = extract_prose_entities(
        "x", PROSE, doc_id="doc-2", source_file="f.docx"
    )

    assert entities_doc1[0].id != entities_doc2[0].id


def test_extract_prose_entities_is_stable_across_repeated_calls(monkeypatch):
    response = json.dumps(
        {"entities": [{"type": "TASK", "name": "Same name"}], "relationships": []}
    )
    _mock_generate(monkeypatch, response)

    first, _ = extract_prose_entities("x", PROSE, doc_id="doc-1", source_file="f.docx")
    second, _ = extract_prose_entities("x", PROSE, doc_id="doc-1", source_file="f.docx")

    assert first[0].id == second[0].id


def test_prompt_only_mentions_target_types(monkeypatch):
    prompts = _mock_generate(monkeypatch, json.dumps({"entities": [], "relationships": []}))

    extract_prose_entities("some prose text", PROSE, doc_id="doc-1", source_file="f.docx")

    assert len(prompts) == 1
    assert "TASK" in prompts[0]
    assert "BUSINESS_TERM" in prompts[0]
    assert "RELATES_TO" in prompts[0]
    assert "some prose text" in prompts[0]
