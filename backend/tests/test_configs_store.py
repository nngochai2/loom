import textwrap

import pytest
import yaml

from app.configs.store import (
    ConfigAlreadyExistsError,
    ConfigNotFoundError,
    ConfigSchemaValidationError,
    ConfigsStore,
    InvalidConfigIdError,
    detect_source_type,
)

DOCX_RULE = {
    "name": "Business Requirements (fixture)",
    "node_label": "REQUIREMENT",
    "id_pattern": r"^BR\s*(\d+)$",
    "category_signals": [{"id": "cs-1", "name": "SQLView", "pattern": "VW_"}],
}

OBSIDIAN_CONFIG = {
    "include_folders": ["Project"],
    "tags_folder": "Tags",
    "main_folder": "Project",
    "subfolder_type_map": {"architecture": "ARCHITECTURE"},
    "type_signals": {"TASK": ["bug", "fix"]},
    "rel_keywords": {"depends on": "DEPENDS_ON"},
}


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_detect_source_type_recognizes_docx_rule_file():
    assert detect_source_type(DOCX_RULE) == "docx"


def test_detect_source_type_recognizes_obsidian_config():
    assert detect_source_type(OBSIDIAN_CONFIG) == "obsidian"


def test_detect_source_type_returns_none_for_unrecognized_shape():
    assert detect_source_type({"nonsense": True}) is None


def test_list_configs_lists_both_docx_and_obsidian_fixtures(tmp_path):
    _write_yaml(tmp_path / "br_requirements.yml", DOCX_RULE)
    _write_yaml(tmp_path / "obsidian_config.yml", OBSIDIAN_CONFIG)
    store = ConfigsStore(str(tmp_path))

    summaries = store.list_configs()

    by_id = {s.id: s for s in summaries}
    assert set(by_id) == {"br_requirements", "obsidian_config"}
    assert by_id["br_requirements"].source_type == "docx"
    assert by_id["br_requirements"].title == "Business Requirements (fixture)"
    assert by_id["obsidian_config"].source_type == "obsidian"
    assert by_id["obsidian_config"].title == "obsidian_config"  # no `name` field: falls back to id


def test_get_config_returns_data_and_json_schema(tmp_path):
    _write_yaml(tmp_path / "br_requirements.yml", DOCX_RULE)
    store = ConfigsStore(str(tmp_path))

    detail = store.get_config("br_requirements")

    assert detail.source_type == "docx"
    assert detail.data["node_label"] == "REQUIREMENT"
    assert detail.json_schema["title"] == "Loom docx parsing rule file"


def test_get_config_raises_not_found_for_unknown_id(tmp_path):
    store = ConfigsStore(str(tmp_path))

    with pytest.raises(ConfigNotFoundError):
        store.get_config("does-not-exist")


def test_create_config_writes_a_valid_config_to_disk(tmp_path):
    store = ConfigsStore(str(tmp_path))

    detail = store.create_config("new_rule", "docx", DOCX_RULE)

    assert detail.id == "new_rule"
    assert (tmp_path / "new_rule.yml").exists()
    on_disk = yaml.safe_load((tmp_path / "new_rule.yml").read_text(encoding="utf-8"))
    assert on_disk == DOCX_RULE


def test_create_config_rejects_docx_rule_missing_id_pattern_and_writes_no_file(tmp_path):
    store = ConfigsStore(str(tmp_path))
    invalid = {"name": "Bad rule", "node_label": "REQUIREMENT"}  # missing id_pattern

    with pytest.raises(ConfigSchemaValidationError):
        store.create_config("bad_rule", "docx", invalid)

    assert not (tmp_path / "bad_rule.yml").exists()
    assert list(tmp_path.iterdir()) == []


def test_create_config_rejects_unknown_source_type(tmp_path):
    store = ConfigsStore(str(tmp_path))

    with pytest.raises(ConfigSchemaValidationError):
        store.create_config("x", "bogus", {"anything": True})


def test_create_config_rejects_duplicate_id(tmp_path):
    store = ConfigsStore(str(tmp_path))
    store.create_config("dup", "docx", DOCX_RULE)

    with pytest.raises(ConfigAlreadyExistsError):
        store.create_config("dup", "docx", DOCX_RULE)


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", "a\\b", "..", "."])
def test_create_config_rejects_path_traversal_ids(tmp_path, bad_id):
    store = ConfigsStore(str(tmp_path))

    with pytest.raises(InvalidConfigIdError):
        store.create_config(bad_id, "docx", DOCX_RULE)


def test_update_config_overwrites_file_and_is_reflected_on_get(tmp_path):
    _write_yaml(tmp_path / "br_requirements.yml", DOCX_RULE)
    store = ConfigsStore(str(tmp_path))
    edited = {**DOCX_RULE, "title_from": "first_line"}

    store.update_config("br_requirements", edited)

    assert store.get_config("br_requirements").data["title_from"] == "first_line"


def test_update_config_raises_not_found_for_unknown_id(tmp_path):
    store = ConfigsStore(str(tmp_path))

    with pytest.raises(ConfigNotFoundError):
        store.update_config("does-not-exist", DOCX_RULE)


def test_update_config_rejects_invalid_edit_and_leaves_file_untouched(tmp_path):
    _write_yaml(tmp_path / "br_requirements.yml", DOCX_RULE)
    store = ConfigsStore(str(tmp_path))
    original_bytes = (tmp_path / "br_requirements.yml").read_bytes()

    with pytest.raises(ConfigSchemaValidationError):
        store.update_config("br_requirements", {"name": "no node_label or id_pattern"})

    assert (tmp_path / "br_requirements.yml").read_bytes() == original_bytes


def test_load_then_unchanged_save_round_trips_data_stably(tmp_path):
    # Loading a config and immediately saving it back unchanged must not
    # churn the data (formatting aside, per the ticket's acceptance
    # criterion) -- and re-saving the same data twice must be idempotent.
    rule_yaml = textwrap.dedent(
        """\
        name: "Business Requirements (fixture)"
        node_label: REQUIREMENT
        id_pattern: '^BR\\s*(\\d+)$'
        category_signals:
          - id: cs-1
            name: SQLView
            pattern: 'VW_'
        """
    )
    (tmp_path / "br_requirements.yml").write_text(rule_yaml, encoding="utf-8")
    store = ConfigsStore(str(tmp_path))

    loaded = store.get_config("br_requirements")
    store.update_config("br_requirements", loaded.data)
    first_write = (tmp_path / "br_requirements.yml").read_bytes()

    store.update_config("br_requirements", store.get_config("br_requirements").data)
    second_write = (tmp_path / "br_requirements.yml").read_bytes()

    assert first_write == second_write
    assert yaml.safe_load(first_write) == yaml.safe_load(rule_yaml)
