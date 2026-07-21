"""The preview endpoint (spec §4.1 design test, ticket #9): exercised
through `app.main.create_app` the same way `test_api_configs.py`/
`test_api_jobs.py` are, pointed at the real fixture set under
`tests/fixtures` (the same fixtures the golden-parity gate uses) since a
named-fixture preview needs real discoverable documents, not fakes.
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline.registry import SINKS, SOURCES
from fakes_jobs import RecordingSink

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@contextmanager
def _client(
    configs_dir: str,
    fixtures_dir: str = str(FIXTURES_DIR),
    sinks: dict[str, object] = SINKS,
) -> Iterator[TestClient]:
    app = create_app(
        db_path=":memory:",
        configs_dir=configs_dir,
        fixtures_dir=fixtures_dir,
        sources=SOURCES,
        sinks=sinks,  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        yield client


def _copy_docx_config(configs_dir: Path) -> None:
    shutil.copy(FIXTURES_DIR / "br_requirements.yml", configs_dir / "br_requirements.yml")


def _copy_obsidian_config(configs_dir: Path) -> None:
    shutil.copy(FIXTURES_DIR / "obsidian_config.yml", configs_dir / "obsidian_config.yml")


def _poll_until_terminal(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] in ("completed", "failed", "cancelled"):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"job {job_id} never reached a terminal status: {body}")
        time.sleep(0.02)


def test_preview_against_a_docx_fixture_returns_extracted_entities(tmp_path):
    _copy_docx_config(tmp_path)

    with _client(str(tmp_path)) as client:
        resp = client.post(
            "/configs/br_requirements/preview", data={"fixture_id": "with_tables.docx"}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["doc_id"]
    assert len(body["entities"]) == 2
    assert all(e["type"] == "REQUIREMENT" for e in body["entities"])


def test_preview_against_an_obsidian_fixture_resolves_wikilinks_across_the_vault(tmp_path):
    # Proves preview runs discovery over the *whole* vault, not an isolated
    # copy of just the target note -- if it only saw "Fix Login Bug.md" in
    # isolation, both wikilinks below would dangle instead of resolving.
    _copy_obsidian_config(tmp_path)

    with _client(str(tmp_path)) as client:
        resp = client.post(
            "/configs/obsidian_config/preview",
            data={"fixture_id": "Project/Tasks/Fix Login Bug.md"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entities"]) == 1
    assert body["entities"][0]["name"] == "Fix Login Bug"
    assert len(body["relationships"]) == 2  # Auth Service + API Gateway; Ghost Note dangles
    assert all(r["origin"] == "explicit" for r in body["relationships"])


def test_preview_rejects_unknown_fixture_id(tmp_path):
    _copy_docx_config(tmp_path)

    with _client(str(tmp_path)) as client:
        resp = client.post(
            "/configs/br_requirements/preview", data={"fixture_id": "does-not-exist.docx"}
        )

    assert resp.status_code == 404


def test_preview_404s_for_unknown_config(tmp_path):
    with _client(str(tmp_path)) as client:
        resp = client.post("/configs/does-not-exist/preview", data={"fixture_id": "x"})

    assert resp.status_code == 404


def test_preview_rejects_neither_fixture_nor_upload(tmp_path):
    _copy_docx_config(tmp_path)

    with _client(str(tmp_path)) as client:
        resp = client.post("/configs/br_requirements/preview")

    assert resp.status_code == 422


def test_preview_rejects_both_fixture_and_upload(tmp_path):
    _copy_docx_config(tmp_path)
    content = (FIXTURES_DIR / "docs" / "with_tables.docx").read_bytes()

    with _client(str(tmp_path)) as client:
        resp = client.post(
            "/configs/br_requirements/preview",
            data={"fixture_id": "with_tables.docx"},
            files={"sample": ("upload.docx", content, "application/octet-stream")},
        )

    assert resp.status_code == 422


def test_preview_accepts_an_uploaded_one_off_sample_document(tmp_path):
    _copy_docx_config(tmp_path)
    content = (FIXTURES_DIR / "docs" / "with_tables.docx").read_bytes()

    with _client(str(tmp_path)) as client:
        resp = client.post(
            "/configs/br_requirements/preview",
            files={"sample": ("one_off_sample.docx", content, "application/octet-stream")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entities"]) == 2
    assert all(e["type"] == "REQUIREMENT" for e in body["entities"])


def test_preview_writes_nothing_anywhere_no_sink_other_than_dryrun_is_ever_touched(tmp_path):
    # A `neo4j`/`chroma` sink is never even instantiated for a preview run
    # -- DryRunSink is hardcoded as the only sink `_run_dryrun_for_doc`
    # constructs -- so there is no write path to assert against; this test
    # documents that guarantee by using a `sinks` registry preview never
    # consults, proving nothing outside `DryRunSink` is reachable.
    _copy_docx_config(tmp_path)

    def _boom() -> object:
        raise AssertionError("preview must never touch any sink but its own DryRunSink")

    with _client(str(tmp_path), sinks={"neo4j": _boom}) as client:  # type: ignore[dict-item]
        resp = client.post(
            "/configs/br_requirements/preview", data={"fixture_id": "with_tables.docx"}
        )

    assert resp.status_code == 200


def test_preview_matches_a_real_job_for_the_same_doc_and_config(tmp_path):
    # The ticket's acceptance criterion (spec §4.1 design test): run the
    # identical doc+config through a real job -- recorded by a sink that's
    # deliberately *not* preview's own `DryRunSink` class, so this doesn't
    # just compare `DryRunSink` against itself -- and through preview, and
    # assert the two extractions are identical on every field the client
    # actually observes.
    _copy_docx_config(tmp_path)
    shared_sink = RecordingSink()

    with _client(str(tmp_path), sinks={"recording": lambda: shared_sink}) as client:
        job_resp = client.post(
            "/jobs",
            json={
                "source_type": "docx",
                "source_path": str(FIXTURES_DIR / "docs"),
                "sinks": ["recording"],
                "config_id": str(FIXTURES_DIR / "br_requirements.yml"),
            },
        )
        assert job_resp.status_code == 201
        _poll_until_terminal(client, job_resp.json()["job_id"])

        preview_resp = client.post(
            "/configs/br_requirements/preview", data={"fixture_id": "with_tables.docx"}
        )

    assert preview_resp.status_code == 200
    preview_body = preview_resp.json()

    job_result_for_doc = dict(shared_sink.writes)[preview_body["doc_id"]]
    assert preview_body["content_hash"] == job_result_for_doc.content_hash
    assert {(e["id"], e["type"], e["name"], e["origin"], e["rule_id"]) for e in preview_body["entities"]} == {
        (e.id, e.type, e.name, e.origin, e.rule_id) for e in job_result_for_doc.entities
    }
    assert {
        (r["from_id"], r["to_id"], r["type"], r["origin"], r["rule_id"]) for r in preview_body["relationships"]
    } == {(r.from_id, r.to_id, r.type, r.origin, r.rule_id) for r in job_result_for_doc.relationships}
