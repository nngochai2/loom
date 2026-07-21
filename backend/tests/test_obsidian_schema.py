import pytest
from jsonschema import ValidationError

from app.pipeline.sources.obsidian_schema import validate_obsidian_config

VALID_CONFIG = {
    "include_folders": ["Project"],
    "tags_folder": "Tags",
    "main_folder": "Project",
    "subfolder_type_map": {"architecture": "ARCHITECTURE"},
    "type_signals": {"TASK": ["bug", "fix"]},
    "rel_keywords": {"depends on": "DEPENDS_ON"},
}


def test_validate_obsidian_config_accepts_a_valid_config():
    validate_obsidian_config(VALID_CONFIG)  # does not raise


def test_validate_obsidian_config_accepts_an_optional_name():
    validate_obsidian_config({**VALID_CONFIG, "name": "My vault"})  # does not raise


def test_validate_obsidian_config_rejects_missing_required_field():
    raw = {k: v for k, v in VALID_CONFIG.items() if k != "rel_keywords"}

    with pytest.raises(ValidationError):
        validate_obsidian_config(raw)


def test_validate_obsidian_config_rejects_unknown_properties():
    with pytest.raises(ValidationError):
        validate_obsidian_config({**VALID_CONFIG, "unexpected_field": True})


def test_validate_obsidian_config_rejects_wrong_type_for_rel_keywords():
    with pytest.raises(ValidationError):
        validate_obsidian_config({**VALID_CONFIG, "rel_keywords": ["not", "a", "map"]})
