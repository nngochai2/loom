import textwrap

import pytest
from jsonschema import ValidationError

from app.pipeline.rules.schema import load_rule_file, validate_rule_file

RULE_YAML = textwrap.dedent(
    """\
    name: "Business Requirements (fixture)"
    node_label: REQUIREMENT
    id_pattern: '^BR\\s*(\\d+)$'
    id_flags: IGNORECASE
    id_format: 'BR{:02d}'
    title_from: first_line
    category_signals:
      - id: cs-sql-view
        name: SQLView
        pattern: '\\bVW_[A-Z_]+\\b'
      - id: cs-batch-job
        name: BatchJob
        pattern: '\\bbatch\\s*job\\b'
        flags: IGNORECASE
    named_extractions:
      - id: ne-views
        name: views
        pattern: '\\bVW_[A-Z_]+\\b'
        flags: IGNORECASE
        transform: uppercase
        deduplicate: true
        sort: true
      - id: ne-fields
        name: fields
        pattern: '"([^"]{3,60})"'
        group: 1
        filter: no_spaces
        deduplicate: true
        sort: true
    context:
      include_paragraphs: true
      include_non_br_tables: true
    """
)


def _write(tmp_path, content: str):
    path = tmp_path / "rule.yml"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_rule_file_parses_all_fields(tmp_path):
    path = _write(tmp_path, RULE_YAML)

    rule = load_rule_file(str(path))

    assert rule.name == "Business Requirements (fixture)"
    assert rule.node_label == "REQUIREMENT"
    assert rule.id_pattern == r"^BR\s*(\d+)$"
    assert rule.id_flags == "IGNORECASE"
    assert rule.id_format == "BR{:02d}"
    assert rule.title_from == "first_line"
    assert [s.id for s in rule.category_signals] == ["cs-sql-view", "cs-batch-job"]
    assert [s.name for s in rule.category_signals] == ["SQLView", "BatchJob"]
    assert rule.category_signals[1].flags == "IGNORECASE"
    assert [e.id for e in rule.named_extractions] == ["ne-views", "ne-fields"]
    assert rule.named_extractions[0].transform == "uppercase"
    assert rule.named_extractions[0].deduplicate is True
    assert rule.named_extractions[0].sort is True
    assert rule.named_extractions[1].group == 1
    assert rule.named_extractions[1].filter == "no_spaces"
    assert rule.context.include_paragraphs is True
    assert rule.context.include_non_br_tables is True


def test_load_rule_file_defaults_context_and_signals_when_omitted(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            name: "Minimal rule"
            node_label: REQUIREMENT
            id_pattern: '^BR\\s*(\\d+)$'
            """
        ),
    )

    rule = load_rule_file(str(path))

    assert rule.category_signals == ()
    assert rule.named_extractions == ()
    assert rule.context.include_paragraphs is True
    assert rule.context.include_non_br_tables is True
    assert rule.id_format == "{}"


def test_validate_rule_file_rejects_missing_node_label():
    raw = {"name": "x", "id_pattern": "^BR(\\d+)$"}

    with pytest.raises(ValidationError):
        validate_rule_file(raw)


def test_validate_rule_file_rejects_category_signal_missing_id():
    raw = {
        "name": "x",
        "node_label": "REQUIREMENT",
        "id_pattern": "^BR(\\d+)$",
        "category_signals": [{"name": "SQLView", "pattern": "VW_"}],
    }

    with pytest.raises(ValidationError):
        validate_rule_file(raw)


def test_validate_rule_file_rejects_duplicate_ids_across_signals_and_extractions():
    raw = {
        "name": "x",
        "node_label": "REQUIREMENT",
        "id_pattern": "^BR(\\d+)$",
        "category_signals": [{"id": "dup", "name": "SQLView", "pattern": "VW_"}],
        "named_extractions": [{"id": "dup", "name": "views", "pattern": "VW_"}],
    }

    with pytest.raises(ValueError, match="duplicate rule id"):
        validate_rule_file(raw)


def test_validate_rule_file_accepts_unique_ids():
    raw = {
        "name": "x",
        "node_label": "REQUIREMENT",
        "id_pattern": "^BR(\\d+)$",
        "category_signals": [{"id": "cs-1", "name": "SQLView", "pattern": "VW_"}],
        "named_extractions": [{"id": "ne-1", "name": "views", "pattern": "VW_"}],
    }

    validate_rule_file(raw)  # does not raise


# ── context.prose_extraction (ADR-0018) ──────────────────────────────────────


def test_load_rule_file_parses_prose_extraction_block(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            name: "Business Requirements (fixture)"
            node_label: REQUIREMENT
            id_pattern: '^BR\\s*(\\d+)$'
            context:
              prose_extraction:
                enabled: true
                id: pe-intro
                target_entity_types: [TASK, BUSINESS_TERM]
                target_relationship_types: [RELATES_TO, DEPENDS_ON]
            """
        ),
    )

    rule = load_rule_file(str(path))

    assert rule.context.prose_extraction.enabled is True
    assert rule.context.prose_extraction.id == "pe-intro"
    assert rule.context.prose_extraction.target_entity_types == ("TASK", "BUSINESS_TERM")
    assert rule.context.prose_extraction.target_relationship_types == (
        "RELATES_TO",
        "DEPENDS_ON",
    )


def test_load_rule_file_defaults_prose_extraction_when_omitted(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            name: "Minimal rule"
            node_label: REQUIREMENT
            id_pattern: '^BR\\s*(\\d+)$'
            """
        ),
    )

    rule = load_rule_file(str(path))

    assert rule.context.prose_extraction.enabled is False
    assert rule.context.prose_extraction.id == ""
    assert rule.context.prose_extraction.target_entity_types == ()
    assert rule.context.prose_extraction.target_relationship_types == ()


def test_validate_rule_file_rejects_prose_extraction_missing_id():
    raw = {
        "name": "x",
        "node_label": "REQUIREMENT",
        "id_pattern": "^BR(\\d+)$",
        "context": {"prose_extraction": {"enabled": True}},
    }

    with pytest.raises(ValidationError):
        validate_rule_file(raw)


def test_validate_rule_file_rejects_unknown_target_entity_type():
    raw = {
        "name": "x",
        "node_label": "REQUIREMENT",
        "id_pattern": "^BR(\\d+)$",
        "context": {
            "prose_extraction": {
                "id": "pe-1",
                "target_entity_types": ["NOT_A_REAL_TYPE"],
            }
        },
    }

    with pytest.raises(ValidationError):
        validate_rule_file(raw)


def test_validate_rule_file_rejects_unknown_target_relationship_type():
    raw = {
        "name": "x",
        "node_label": "REQUIREMENT",
        "id_pattern": "^BR(\\d+)$",
        "context": {
            "prose_extraction": {
                "id": "pe-1",
                "target_relationship_types": ["NOT_A_REAL_TYPE"],
            }
        },
    }

    with pytest.raises(ValidationError):
        validate_rule_file(raw)


def test_validate_rule_file_rejects_prose_extraction_id_colliding_with_category_signal():
    raw = {
        "name": "x",
        "node_label": "REQUIREMENT",
        "id_pattern": "^BR(\\d+)$",
        "category_signals": [{"id": "dup", "name": "SQLView", "pattern": "VW_"}],
        "context": {"prose_extraction": {"id": "dup"}},
    }

    with pytest.raises(ValueError, match="duplicate rule id"):
        validate_rule_file(raw)


def test_validate_rule_file_accepts_prose_extraction_with_unique_id():
    raw = {
        "name": "x",
        "node_label": "REQUIREMENT",
        "id_pattern": "^BR(\\d+)$",
        "category_signals": [{"id": "cs-1", "name": "SQLView", "pattern": "VW_"}],
        "context": {
            "prose_extraction": {
                "enabled": True,
                "id": "pe-1",
                "target_entity_types": ["TASK"],
            }
        },
    }

    validate_rule_file(raw)  # does not raise
