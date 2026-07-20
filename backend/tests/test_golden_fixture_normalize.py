"""Unit tests for the pure normalization/diff seam (`golden_fixture_normalize`)
used by the golden-fixture parity gate (ADR-0007). Exercised with synthetic
data first, per TDD, before `test_golden_fixture_parity.py` wires in the
real committed snapshot and Loom's real adapters.
"""

from pathlib import PurePosixPath

from golden_fixture_normalize import (
    DOCX_NODE_LABEL_ALLOWLIST,
    LOOM_ONLY_ENTITY_FIELDS,
    LOOM_ONLY_RELATIONSHIP_FIELDS,
    diff_records,
    docx_entity_key,
    edge_key,
    entity_key,
    naa_note_id,
    normalize_loom_docx_entity,
    normalize_loom_obsidian_edge,
    normalize_loom_obsidian_entity,
    normalize_naa_docx_item,
    normalize_naa_obsidian_edge,
    normalize_naa_obsidian_note,
)
from app.pipeline.types import Entity, Relationship


class _FakeNaaNote:
    def __init__(self, path, note_type, title, subfolder="", status="", created_at=""):
        self.path = PurePosixPath(path)
        self.note_type = note_type
        self.title = title
        self.subfolder = subfolder
        self.status = status
        self.created_at = created_at


class _FakeNaaWikiLink:
    def __init__(self, relationship, alias, context):
        self.relationship = relationship
        self.alias = alias
        self.context = context


class _FakeNaaDocxItem:
    def __init__(self, node_label, req_id, title, body, source_file, categories, extractions):
        self.node_label = node_label
        self.req_id = req_id
        self.title = title
        self.body = body
        self.source_file = source_file
        self.candidate_categories = categories
        self.named_extractions = extractions


# ── diff_records: the core pure comparison seam ────────────────────────────


def test_diff_records_reports_nothing_when_both_sides_match():
    golden = [{"id": "a", "type": "TASK"}]
    actual = [{"id": "a", "type": "TASK"}]

    assert diff_records(golden, actual, entity_key, "entity") == []


def test_diff_records_reports_a_field_mismatch():
    golden = [{"id": "a", "type": "TASK"}]
    actual = [{"id": "a", "type": "ARCHITECTURE"}]

    problems = diff_records(golden, actual, entity_key, "entity")

    assert len(problems) == 1
    assert "'a'" in problems[0]


def test_diff_records_reports_a_record_only_naa_produced():
    golden = [{"id": "a", "type": "TASK"}]
    actual = []

    problems = diff_records(golden, actual, entity_key, "entity")

    assert len(problems) == 1
    assert "NAA produced" in problems[0]


def test_diff_records_reports_a_record_only_loom_produced():
    golden = []
    actual = [{"id": "a", "type": "TASK"}]

    problems = diff_records(golden, actual, entity_key, "entity")

    assert len(problems) == 1
    assert "Loom's adapter produced" in problems[0]


def test_diff_records_uses_the_supplied_key_fn_not_full_equality():
    # Edges are keyed by (from_id, to_id, type) -- extra fields (alias,
    # context) can differ in the diff message without affecting matching.
    golden = [{"from_id": "x", "to_id": "y", "type": "LINKS_TO", "alias": "Y"}]
    actual = [{"from_id": "x", "to_id": "y", "type": "LINKS_TO", "alias": "Y (alias)"}]

    problems = diff_records(golden, actual, edge_key, "edge")

    assert len(problems) == 1  # matched by key, but field mismatch reported
    assert "mismatch" in problems[0]


# ── Obsidian normalization: Loom's shape must line up with NAA's ──────────


def test_normalize_loom_and_naa_obsidian_entity_agree_on_shape():
    note = _FakeNaaNote(
        path="Project/Architecture/Auth Service.md",
        note_type="ARCHITECTURE",
        title="Auth Service",
        subfolder="Architecture",
        status="open",
        created_at="2024-01-01T10:00",
    )
    entity = Entity(
        id=naa_note_id(note),
        type="ARCHITECTURE",
        name="Auth Service",
        origin="extracted",
        rule_id="subfolder:architecture",
        properties={"subfolder": "Architecture", "status": "open", "header_created_at": "2024-01-01T10:00"},
    )

    assert normalize_loom_obsidian_entity(entity) == normalize_naa_obsidian_note(note)


def test_naa_note_id_is_stable_regardless_of_os_path_separator_style():
    # The whole point of naa_note_id() over NAA's own note.node_id
    # property: it must hash identically however the Path was
    # constructed, so a golden snapshot built on Windows still matches
    # what Loom's adapter (relative_path.as_posix()) computes in CI.
    note = _FakeNaaNote(path="Project/Architecture/Auth Service.md", note_type="X", title="X")

    assert naa_note_id(note) == "61e6f8ff245aa497"


def test_normalize_loom_obsidian_entity_never_leaks_loom_only_fields():
    # Acceptance criterion 1: origin/rule_id are schema metadata Loom adds
    # that NAA has no concept of -- normalization must drop them, not
    # silently pass them through into the comparison.
    entity = Entity(id="x", type="TASK", name="X", origin="extracted", rule_id="default")

    normalized = normalize_loom_obsidian_entity(entity)

    assert not set(normalized) & set(LOOM_ONLY_ENTITY_FIELDS)


def test_normalize_loom_obsidian_edge_never_leaks_loom_only_fields():
    rel = Relationship(from_id="a", to_id="b", type="LINKS_TO", origin="explicit")

    normalized = normalize_loom_obsidian_edge(rel)

    assert not set(normalized) & set(LOOM_ONLY_RELATIONSHIP_FIELDS)


def test_normalize_loom_obsidian_entity_defaults_missing_properties_to_empty_string():
    # Tag-folder entities carry no properties dict at all.
    entity = Entity(id="tagid", type="TAG", name="backend", origin="extracted", rule_id="tag-folder")

    normalized = normalize_loom_obsidian_entity(entity)

    assert normalized["subfolder"] == ""
    assert normalized["status"] == ""
    assert normalized["created_at"] == ""


def test_normalize_loom_and_naa_obsidian_edge_agree_on_shape():
    rel = Relationship(
        from_id="a",
        to_id="b",
        type="DEPENDS_ON",
        origin="explicit",
        properties={"alias": "API Gateway", "context": "depends on [[API Gateway]] for routing"},
    )
    link = _FakeNaaWikiLink(
        relationship="DEPENDS_ON", alias="API Gateway", context="depends on [[API Gateway]] for routing"
    )

    assert normalize_loom_obsidian_edge(rel) == normalize_naa_obsidian_edge("a", "b", link)


# ── Docx normalization: the node_label allowlist is real, visible code ────


def test_docx_node_label_allowlist_maps_naas_br_to_looms_requirement():
    assert DOCX_NODE_LABEL_ALLOWLIST == {"BR": "REQUIREMENT"}


def test_normalize_loom_and_naa_docx_entity_agree_after_allowlist_mapping():
    entity = Entity(
        id="deadbeef",
        type="REQUIREMENT",
        name="The system must do X",
        origin="extracted",
        rule_id="id-pattern-match",
        properties={
            "req_id": "BR01",
            "body": "The system must do X",
            "source_file": "with_tables.docx",
            "candidate_categories": ["BatchJob", "SQLView"],
            "named_extractions": {"views": ["VW_X"], "fields": []},
        },
    )
    item = _FakeNaaDocxItem(
        node_label="BR",  # NAA's real, unmapped label
        req_id="BR01",
        title="The system must do X",
        body="The system must do X",
        source_file="with_tables.docx",
        categories=["SQLView", "BatchJob"],  # different order -- sorted away
        extractions={"views": ["VW_X"], "fields": []},
    )

    assert normalize_loom_docx_entity(entity) == normalize_naa_docx_item(item)


def test_docx_entity_key_ignores_the_differing_id_schemes():
    # Loom hashes node_label::doc_id::req_id; NAA hashes
    # node_label::source_label::source_file::req_id. Neither raw id is
    # part of the key -- only (source_file, req_id).
    loom_record = {"source_file": "with_tables.docx", "req_id": "BR01"}
    naa_record = {"source_file": "with_tables.docx", "req_id": "BR01"}

    assert docx_entity_key(loom_record) == docx_entity_key(naa_record)
