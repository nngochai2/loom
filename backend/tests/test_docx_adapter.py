"""Tests for the docx source adapter against the checked-in fixture set
(spec §11: one plain-prose docx, one with a requirements table, one that
intentionally triggers zero extractions) and its rule file
(tests/fixtures/br_requirements.yml).
"""

import dataclasses
from pathlib import Path

import pytest

from app.pipeline.rules.schema import RuleContext, load_rule_file
from app.pipeline.sources.docx import DocxSourceAdapter

FIXTURES_DIR = Path(__file__).parent / "fixtures"
DOCS_DIR = FIXTURES_DIR / "docs"
RULE_PATH = FIXTURES_DIR / "br_requirements.yml"


@pytest.fixture()
def adapter() -> DocxSourceAdapter:
    return DocxSourceAdapter(load_rule_file(str(RULE_PATH)))


def _doc_for(docs, filename: str):
    return next(d for d in docs if d.path.endswith(filename))


def test_discover_finds_every_docx_file(adapter):
    docs = adapter.discover(str(DOCS_DIR))

    names = {Path(d.path).name for d in docs}
    assert names == {"plain_prose.docx", "with_tables.docx", "zero_extraction.docx"}


def test_discover_skips_word_lock_files(adapter, tmp_path):
    (tmp_path / "~$scratch.docx").write_bytes(b"not a real docx")
    docs = adapter.discover(str(tmp_path))

    assert docs == []


def test_discover_computes_stable_content_hash(adapter):
    first = {d.doc_id: d.content_hash for d in adapter.discover(str(DOCS_DIR))}
    second = {d.doc_id: d.content_hash for d in adapter.discover(str(DOCS_DIR))}

    assert first == second
    assert all(h for h in first.values())


def test_plain_prose_fixture_yields_zero_entities_and_relationships(adapter):
    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "plain_prose.docx")

    loaded = adapter.load(doc)
    result = adapter.extract(loaded, adapter.rule_file)

    assert result.entities == ()
    assert result.relationships == ()
    # No table at all, but paragraph text is still collected as content
    # (potential future vector-sink input, ADR-0012) — proves load() ran,
    # not that extraction was skipped entirely.
    assert "onboarding" in loaded.content.lower()


def test_zero_extraction_fixture_yields_nothing_despite_a_table_being_present(adapter):
    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "zero_extraction.docx")

    loaded = adapter.load(doc)
    result = adapter.extract(loaded, adapter.rule_file)

    assert result.entities == ()
    assert result.relationships == ()
    # The table's ref "XYZ-1" doesn't match id_pattern, so its rows are
    # folded into context text instead of producing an item.
    assert "XYZ-1" in loaded.content


def test_with_tables_fixture_extracts_requirement_entities_from_matching_rows(adapter):
    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "with_tables.docx")

    loaded = adapter.load(doc)
    result = adapter.extract(loaded, adapter.rule_file)

    assert len(result.entities) == 2
    req_ids = {e.properties["req_id"] for e in result.entities}
    assert req_ids == {"BR01", "BR02"}
    assert all(e.type == "REQUIREMENT" for e in result.entities)
    assert all(e.origin == "extracted" for e in result.entities)
    assert all(e.rule_id == "id-pattern-match" for e in result.entities)

    br01 = next(e for e in result.entities if e.properties["req_id"] == "BR01")
    assert set(br01.properties["candidate_categories"]) == {"SQLView", "BatchJob"}
    assert br01.properties["named_extractions"]["views"] == ["VW_INVOICE_HDR"]
    assert br01.properties["named_extractions"]["fields"] == ["INVOICE_DATE"]

    # The non-BR glossary table in the same doc doesn't produce entities.
    assert result.relationships == ()


def test_with_tables_fixture_glossary_table_is_collected_as_context_not_entities(adapter):
    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "with_tables.docx")

    loaded = adapter.load(doc)

    assert "Invoice" in loaded.content
    assert "billing document" in loaded.content


def test_extraction_result_carries_doc_id_and_content_hash(adapter):
    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "with_tables.docx")

    loaded = adapter.load(doc)
    result = adapter.extract(loaded, adapter.rule_file)

    assert result.doc_id == doc.doc_id
    assert result.content_hash == doc.content_hash


def test_load_excludes_paragraphs_when_include_paragraphs_is_false():
    rule_file = dataclasses.replace(
        load_rule_file(str(RULE_PATH)),
        context=RuleContext(include_paragraphs=False, include_non_br_tables=True),
    )
    adapter = DocxSourceAdapter(rule_file)
    docs = adapter.discover(str(DOCS_DIR))

    loaded = adapter.load(_doc_for(docs, "with_tables.docx"))

    assert "Additional context follows" not in loaded.content
    assert "Invoice" in loaded.content  # non-BR table still included


def test_load_excludes_non_br_tables_when_include_non_br_tables_is_false():
    rule_file = dataclasses.replace(
        load_rule_file(str(RULE_PATH)),
        context=RuleContext(include_paragraphs=True, include_non_br_tables=False),
    )
    adapter = DocxSourceAdapter(rule_file)
    docs = adapter.discover(str(DOCS_DIR))

    loaded = adapter.load(_doc_for(docs, "with_tables.docx"))

    assert "Additional context follows" in loaded.content
    assert "Invoice" not in loaded.content  # non-BR (glossary) table excluded


def test_load_still_extracts_id_matching_rows_regardless_of_context_flags():
    # Context flags only gate what's collected as free-text context; the
    # id-matching requirements table is always extracted.
    rule_file = dataclasses.replace(
        load_rule_file(str(RULE_PATH)),
        context=RuleContext(include_paragraphs=False, include_non_br_tables=False),
    )
    adapter = DocxSourceAdapter(rule_file)
    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "with_tables.docx")

    loaded = adapter.load(doc)
    result = adapter.extract(loaded, rule_file)

    assert len(result.entities) == 2


def test_entity_ids_are_stable_across_repeated_ingestion(adapter):
    docs = adapter.discover(str(DOCS_DIR))
    doc = _doc_for(docs, "with_tables.docx")

    first = adapter.extract(adapter.load(doc), adapter.rule_file)
    second = adapter.extract(adapter.load(doc), adapter.rule_file)

    assert {e.id for e in first.entities} == {e.id for e in second.entities}
