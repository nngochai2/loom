import importlib

import pytest

kg_schema = importlib.import_module("kg-schema")

EXPECTED_ENTITY_TYPES = (
    "ARCHITECTURE",
    "CONVENTION",
    "TASK",
    "BUSINESS_TERM",
    "NOTE",
    "TAG",
    "REQUIREMENT",
)

EXPECTED_RELATIONSHIP_TYPES = (
    "DEPENDS_ON",
    "EXTENDS",
    "USES",
    "CONNECTS_TO",
    "IMPLEMENTS",
    "RELATES_TO",
    "FIXES",
    "RESOLVES",
    "CAUSED_BY",
    "FOLLOWS",
    "VIOLATES",
    "LINKS_TO",
    "TAGGED_WITH",
)

EXPECTED_MANDATORY_PROPERTY_NAMES = (
    "origin",
    "source_doc",
    "content_hash",
    "rule_id",
    "schema_version",
    "created_at",
    "updated_at",
)


def test_entity_types_match_spec_section_5():
    assert kg_schema.ENTITY_TYPES == EXPECTED_ENTITY_TYPES


def test_relationship_types_match_spec_section_5():
    assert kg_schema.RELATIONSHIP_TYPES == EXPECTED_RELATIONSHIP_TYPES


def test_default_relationship_type_is_links_to():
    assert kg_schema.DEFAULT_RELATIONSHIP_TYPE == "LINKS_TO"
    assert kg_schema.DEFAULT_RELATIONSHIP_TYPE in kg_schema.RELATIONSHIP_TYPES


def test_mandatory_property_names_match_spec_section_5():
    assert kg_schema.MANDATORY_PROPERTY_NAMES == EXPECTED_MANDATORY_PROPERTY_NAMES


def test_schema_version_is_read_from_version_file():
    assert kg_schema.SCHEMA_VERSION == (kg_schema._VERSION_PATH.read_text().strip())


@pytest.mark.parametrize(
    "origin,absent_properties",
    [
        ("curated", {"source_doc", "content_hash", "rule_id"}),
        ("explicit", {"rule_id"}),
        ("extracted", set()),
    ],
)
def test_absent_for_origins_matches_spec_notes(origin, absent_properties):
    actually_absent = {
        prop["name"]
        for prop in kg_schema.MANDATORY_PROPERTIES
        if origin in prop.get("absent_for_origins", ())
    }
    assert actually_absent == absent_properties
