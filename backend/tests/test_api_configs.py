"""The Configs API (spec §7, §8, ticket #8): CRUD over parsing-rule config
YAML on disk, exercised the same way `test_api_jobs.py` exercises the Jobs
API -- through `app.main.create_app`, here pointed at a `tmp_path` configs
directory instead of the real one.
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import yaml
from fastapi.testclient import TestClient

from app.main import create_app

FIXTURES_DIR = Path(__file__).parent / "fixtures"

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


@contextmanager
def _client(configs_dir: str) -> Iterator[TestClient]:
    app = create_app(db_path=":memory:", configs_dir=configs_dir, sources={}, sinks={})
    with TestClient(app) as client:
        yield client


def test_get_configs_lists_the_obsidian_and_docx_fixtures(tmp_path):
    (tmp_path / "br_requirements.yml").write_text(yaml.safe_dump(DOCX_RULE), encoding="utf-8")
    (tmp_path / "obsidian_config.yml").write_text(yaml.safe_dump(OBSIDIAN_CONFIG), encoding="utf-8")

    with _client(str(tmp_path)) as client:
        resp = client.get("/configs")

    assert resp.status_code == 200
    by_id = {c["id"]: c for c in resp.json()["configs"]}
    assert by_id["br_requirements"]["source_type"] == "docx"
    assert by_id["br_requirements"]["title"] == "Business Requirements (fixture)"
    assert by_id["obsidian_config"]["source_type"] == "obsidian"


def test_get_configs_lists_the_real_fixture_files_from_earlier_tickets(tmp_path):
    # The ticket's acceptance criterion names these two files literally --
    # exercise the real fixtures (comments, regex flags, and all) rather
    # than the synthetic dicts used elsewhere in this file, since those
    # already validating is what proves the schemas actually match the
    # docx/Obsidian adapters' real shapes (rules/schema.py, ADR-0004).
    shutil.copy(FIXTURES_DIR / "br_requirements.yml", tmp_path / "br_requirements.yml")
    shutil.copy(FIXTURES_DIR / "obsidian_config.yml", tmp_path / "obsidian_config.yml")

    with _client(str(tmp_path)) as client:
        resp = client.get("/configs")

    assert resp.status_code == 200
    by_id = {c["id"]: c for c in resp.json()["configs"]}
    assert by_id["br_requirements"]["source_type"] == "docx"
    assert by_id["br_requirements"]["title"] == "Business Requirements (fixture)"
    assert by_id["obsidian_config"]["source_type"] == "obsidian"
    assert by_id["obsidian_config"]["title"] == "obsidian_config"


def test_get_config_returns_data_and_json_schema(tmp_path):
    (tmp_path / "br_requirements.yml").write_text(yaml.safe_dump(DOCX_RULE), encoding="utf-8")

    with _client(str(tmp_path)) as client:
        resp = client.get("/configs/br_requirements")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["node_label"] == "REQUIREMENT"
    assert body["json_schema"]["title"] == "Loom docx parsing rule file"


def test_get_config_404s_for_unknown_id(tmp_path):
    with _client(str(tmp_path)) as client:
        resp = client.get("/configs/does-not-exist")

    assert resp.status_code == 404


def test_post_configs_creates_a_valid_config(tmp_path):
    with _client(str(tmp_path)) as client:
        resp = client.post(
            "/configs", json={"id": "new_rule", "source_type": "docx", "data": DOCX_RULE}
        )

    assert resp.status_code == 201
    assert resp.json()["id"] == "new_rule"
    assert (tmp_path / "new_rule.yml").exists()


def test_post_configs_rejects_docx_rule_missing_id_pattern_no_file_written(tmp_path):
    invalid = {"name": "Bad rule", "node_label": "REQUIREMENT"}  # missing id_pattern

    with _client(str(tmp_path)) as client:
        resp = client.post("/configs", json={"id": "bad_rule", "source_type": "docx", "data": invalid})

    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"]
    assert list(tmp_path.iterdir()) == []


def test_post_configs_rejects_duplicate_id(tmp_path):
    with _client(str(tmp_path)) as client:
        first = client.post(
            "/configs", json={"id": "dup", "source_type": "docx", "data": DOCX_RULE}
        )
        assert first.status_code == 201

        second = client.post(
            "/configs", json={"id": "dup", "source_type": "docx", "data": DOCX_RULE}
        )

    assert second.status_code == 409


def test_post_configs_rejects_unsafe_id(tmp_path):
    with _client(str(tmp_path)) as client:
        resp = client.post(
            "/configs", json={"id": "../escape", "source_type": "docx", "data": DOCX_RULE}
        )

    assert resp.status_code == 422


def test_put_configs_updates_in_place_and_get_reflects_the_change(tmp_path):
    (tmp_path / "br_requirements.yml").write_text(yaml.safe_dump(DOCX_RULE), encoding="utf-8")
    edited = {**DOCX_RULE, "title_from": "first_line"}

    with _client(str(tmp_path)) as client:
        put_resp = client.put("/configs/br_requirements", json={"data": edited})
        assert put_resp.status_code == 200

        get_resp = client.get("/configs/br_requirements")

    assert get_resp.json()["data"]["title_from"] == "first_line"


def test_put_configs_404s_for_unknown_id(tmp_path):
    with _client(str(tmp_path)) as client:
        resp = client.put("/configs/does-not-exist", json={"data": DOCX_RULE})

    assert resp.status_code == 404


def test_put_configs_rejects_invalid_edit(tmp_path):
    (tmp_path / "br_requirements.yml").write_text(yaml.safe_dump(DOCX_RULE), encoding="utf-8")

    with _client(str(tmp_path)) as client:
        resp = client.put(
            "/configs/br_requirements", json={"data": {"name": "missing required fields"}}
        )

    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"]


def test_load_then_unchanged_save_round_trips_via_the_api(tmp_path):
    (tmp_path / "obsidian_config.yml").write_text(yaml.safe_dump(OBSIDIAN_CONFIG), encoding="utf-8")

    with _client(str(tmp_path)) as client:
        loaded = client.get("/configs/obsidian_config").json()

        put_resp = client.put("/configs/obsidian_config", json={"data": loaded["data"]})
        assert put_resp.status_code == 200

        reloaded = client.get("/configs/obsidian_config").json()

    assert reloaded["data"] == loaded["data"]
