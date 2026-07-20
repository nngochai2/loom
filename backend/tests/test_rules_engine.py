"""Tests for the docx rule engine (pipeline/rules/engine.py), ported from
NAA's DocxRuleParser row-matching/extraction logic (ADR-0001) and extended
with the generic parent-link mechanism (ADR-0006).
"""

import pytest

from app.pipeline.rules.engine import RuleEngine, TableRow
from app.pipeline.rules.schema import CategorySignal, NamedExtraction, RuleFile


def _rule_file(**overrides: object) -> RuleFile:
    defaults: dict[str, object] = dict(
        name="Business Requirements (fixture)",
        node_label="REQUIREMENT",
        id_pattern=r"^BR\s*(\d+)$",
        id_flags="IGNORECASE",
        id_format="BR{:02d}",
        title_from="first_line",
        category_signals=(
            CategorySignal(id="cs-sql-view", name="SQLView", pattern=r"\bVW_[A-Z_]+\b"),
            CategorySignal(id="cs-batch-job", name="BatchJob", pattern=r"\bbatch\s*job\b", flags="IGNORECASE"),
        ),
        named_extractions=(
            NamedExtraction(
                id="ne-views",
                name="views",
                pattern=r"\bVW_[A-Z_]+\b",
                flags="IGNORECASE",
                transform="uppercase",
                deduplicate=True,
                sort=True,
            ),
            NamedExtraction(
                id="ne-fields",
                name="fields",
                pattern=r'"([^"]{3,60})"',
                group=1,
                filter="no_spaces",
                deduplicate=True,
                sort=True,
            ),
        ),
    )
    defaults.update(overrides)
    return RuleFile(**defaults)  # type: ignore[arg-type]


def test_apply_extracts_entity_for_id_matching_row():
    engine = RuleEngine(_rule_file())
    rows = [TableRow(cells=("BR4", "Store the invoice amount.\nMore detail."))]

    entities, relationships = engine.apply(rows, doc_id="doc1", source_file="spec.docx")

    assert len(entities) == 1
    entity = entities[0]
    assert entity.type == "REQUIREMENT"
    assert entity.origin == "extracted"
    assert entity.rule_id == "id-pattern-match"
    assert entity.name == "Store the invoice amount."
    assert entity.properties["req_id"] == "BR04"
    assert relationships == ()


def test_apply_skips_rows_that_dont_match_id_pattern():
    engine = RuleEngine(_rule_file())
    rows = [
        TableRow(cells=("Not an id", "some text")),
        TableRow(cells=("BR9", "A real requirement.")),
    ]

    entities, _ = engine.apply(rows, doc_id="doc1", source_file="spec.docx")

    assert len(entities) == 1
    assert entities[0].properties["req_id"] == "BR09"


def test_apply_skips_rows_with_fewer_than_two_cells():
    engine = RuleEngine(_rule_file())
    rows = [TableRow(cells=("BR4",))]

    entities, _ = engine.apply(rows, doc_id="doc1", source_file="spec.docx")

    assert entities == ()


def test_apply_infers_all_matching_category_signals_not_just_first():
    engine = RuleEngine(_rule_file())
    rows = [TableRow(cells=("BR1", "Read from VW_INVOICE_HDR during the nightly batch job."))]

    entities, _ = engine.apply(rows, doc_id="doc1", source_file="spec.docx")

    assert set(entities[0].properties["candidate_categories"]) == {"SQLView", "BatchJob"}


def test_apply_runs_named_extractions_with_transform_dedup_and_sort():
    engine = RuleEngine(_rule_file())
    rows = [TableRow(cells=("BR1", 'Uses vw_invoice_hdr and VW_INVOICE_HDR and "INVOICE_DATE".'))]

    entities, _ = engine.apply(rows, doc_id="doc1", source_file="spec.docx")

    named = entities[0].properties["named_extractions"]
    assert named["views"] == ["VW_INVOICE_HDR"]  # uppercased + deduped + sorted
    assert named["fields"] == ["INVOICE_DATE"]


def test_apply_named_extraction_filter_drops_values_containing_spaces():
    engine = RuleEngine(_rule_file())
    rows = [TableRow(cells=("BR1", 'Field "Amount Due" should be dropped by the no_spaces filter.'))]

    entities, _ = engine.apply(rows, doc_id="doc1", source_file="spec.docx")

    assert entities[0].properties["named_extractions"]["fields"] == []


def test_apply_falls_back_to_id_cell_as_title_when_body_is_empty():
    engine = RuleEngine(_rule_file())
    rows = [TableRow(cells=("BR2", "   "))]

    entities, _ = engine.apply(rows, doc_id="doc1", source_file="spec.docx")

    assert entities[0].name == "BR2"


def test_apply_creates_parent_relationship_when_parent_node_id_given():
    engine = RuleEngine(_rule_file())
    rows = [TableRow(cells=("BR1", "A requirement."))]

    entities, relationships = engine.apply(
        rows, doc_id="doc1", source_file="spec.docx", parent_node_id="parent-1"
    )

    assert len(relationships) == 1
    rel = relationships[0]
    assert rel.from_id == "parent-1"
    assert rel.to_id == entities[0].id
    assert rel.origin == "extracted"
    assert rel.rule_id == "id-pattern-match"
    assert rel.type == "LINKS_TO"


def test_apply_uses_custom_parent_rel_type_when_given():
    engine = RuleEngine(_rule_file())
    rows = [TableRow(cells=("BR1", "A requirement."))]

    _, relationships = engine.apply(
        rows,
        doc_id="doc1",
        source_file="spec.docx",
        parent_node_id="parent-1",
        parent_rel_type="IMPLEMENTS",
    )

    assert relationships[0].type == "IMPLEMENTS"


def test_entity_id_is_stable_across_repeated_ingestion_of_the_same_row():
    engine = RuleEngine(_rule_file())
    rows = [TableRow(cells=("BR1", "A requirement."))]

    first, _ = engine.apply(rows, doc_id="doc1", source_file="spec.docx")
    second, _ = engine.apply(rows, doc_id="doc1", source_file="spec.docx")

    assert first[0].id == second[0].id


def test_entity_id_differs_across_documents():
    engine = RuleEngine(_rule_file())
    rows = [TableRow(cells=("BR1", "A requirement."))]

    from_doc1, _ = engine.apply(rows, doc_id="doc1", source_file="spec.docx")
    from_doc2, _ = engine.apply(rows, doc_id="doc2", source_file="other.docx")

    assert from_doc1[0].id != from_doc2[0].id


def test_row_matches_id_and_table_contains_id_row():
    engine = RuleEngine(_rule_file())

    assert engine.row_matches_id("BR4") is True
    assert engine.row_matches_id("not an id") is False
    assert engine.table_contains_id_row([TableRow(cells=("not an id", "x"))]) is False
    assert engine.table_contains_id_row([TableRow(cells=("BR4", "x"))]) is True


def test_rule_engine_rejects_node_label_not_in_kg_schema():
    with pytest.raises(ValueError, match="not a kg-schema entity type"):
        RuleEngine(_rule_file(node_label="NOT_A_REAL_TYPE"))
