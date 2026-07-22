"""Recall-oriented (subset-assertion) test gate for the LLM prose-extraction
path (ADR-0021, issue #18) — deliberately not exact-match like the
golden-fixture parity tests (ADR-0007): even at low temperature, LLM
wording/ordering/extra-item variance is expected and tolerated. This gate
fails only if a known-extractable "must contain" item goes missing, or
extraction errors/times out/returns nothing — it never asserts the model
*didn't* produce something extra.

Separate file and fixture type from `test_golden_fixture_parity.py`'s
exact-match machinery on purpose (ADR-0021's own consequence): the
assertion semantics are fundamentally different, so folding this into
that file would blur two gates that need to stay distinguishable.

Needs a real, reachable local Ollama instance (`docker-compose up ollama`
+ the configured model pulled) — skipped otherwise. That's an environment
precondition specific to this one file, distinct from the disposable-Neo4j
requirement the rest of the suite sidesteps entirely by writing to fakes
(spec §11); mocking `ollama_client.generate` here would defeat the point
of a recall gate.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict

import httpx
import pytest

from app.pipeline.rules.schema import load_rule_file
from app.pipeline.sources.docx import DocxSourceAdapter
from app.pipeline.types import Entity, Relationship

FIXTURES_DIR = Path(__file__).parent / "fixtures"
# A dedicated directory, not fixtures/docs -- DocxSourceAdapter.discover()
# recursively globs *.docx, and fixtures/docs's exact doc count/names are
# asserted elsewhere (test_docx_adapter.py, test_cli.py); adding a fixture
# there would silently break those.
DOCS_DIR = FIXTURES_DIR / "prose_recall"
RULE_PATH = FIXTURES_DIR / "prose_recall_fixture.yml"
DOC_NAME = "prose_recall_fixture.docx"

os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("OLLAMA_MODEL", "llama3.1")


class _EntitySpec(TypedDict):
    type: str
    name_contains: str


class _RelationshipSpec(TypedDict):
    type: str
    from_contains: str
    to_contains: str


# ── The curated "must contain" list (ADR-0021) ──────────────────────────────
# Every item here is known-extractable from the prose passage in
# tests/fixtures/docs/prose_recall_fixture.docx:
#
#   "The Invoice Service depends on the Payment Gateway to process customer
#   transactions. New engineers must complete the onboarding checklist
#   before they are granted production access. The Payment Gateway uses
#   OAuth2 for authentication."
#
# Matching is case-insensitive substring containment on entity names, not
# exact match -- the model is free to word things differently, or extract
# additional items beyond this list, without failing the test.

MUST_CONTAIN_ENTITIES: list[_EntitySpec] = [
    {"type": "BUSINESS_TERM", "name_contains": "invoice service"},
    {"type": "BUSINESS_TERM", "name_contains": "payment gateway"},
    {"type": "TASK", "name_contains": "onboarding checklist"},
]

MUST_CONTAIN_RELATIONSHIPS: list[_RelationshipSpec] = [
    {"type": "DEPENDS_ON", "from_contains": "invoice service", "to_contains": "payment gateway"},
]


def _ollama_reachable() -> bool:
    base_url = os.environ["OLLAMA_BASE_URL"]
    try:
        response = httpx.get(f"{base_url}/api/tags", timeout=3.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_reachable(),
    reason=(
        "no reachable local Ollama instance (ADR-0021) -- "
        "run `docker-compose up ollama` and pull the configured model first"
    ),
)


def _extract() -> tuple[tuple[Entity, ...], tuple[Relationship, ...]]:
    rule_file = load_rule_file(str(RULE_PATH))
    adapter = DocxSourceAdapter(rule_file)
    doc = next(d for d in adapter.discover(str(DOCS_DIR)) if d.path.endswith(DOC_NAME))
    result = adapter.extract(adapter.load(doc), adapter.rule_file)
    return result.entities, result.relationships


def _entity_matches(entity: Entity, spec: _EntitySpec) -> bool:
    return entity.type == spec["type"] and spec["name_contains"] in entity.name.lower()


def _relationship_matches(
    rel: Relationship, spec: _RelationshipSpec, entities_by_id: dict[str, Entity]
) -> bool:
    if rel.type != spec["type"]:
        return False
    from_entity = entities_by_id.get(rel.from_id)
    to_entity = entities_by_id.get(rel.to_id)
    if from_entity is None or to_entity is None:
        return False
    return (
        spec["from_contains"] in from_entity.name.lower()
        and spec["to_contains"] in to_entity.name.lower()
    )


def test_recall_fixture_returns_something_and_tags_it_with_the_prose_rule_id():
    # A broken prompt template, an incompatible model swap, or a
    # pipeline-wiring bug all show up here first, before the more specific
    # must-contain checks below (ADR-0021's "returns nothing" failure mode).
    entities, relationships = _extract()

    assert entities, "prose extraction returned zero entities -- extraction is broken"
    assert all(e.rule_id == "pe-recall-fixture" for e in entities)
    assert all(r.rule_id == "pe-recall-fixture" for r in relationships)


def test_recall_fixture_extracts_every_must_contain_entity():
    entities, _relationships = _extract()

    for spec in MUST_CONTAIN_ENTITIES:
        assert any(_entity_matches(e, spec) for e in entities), (
            f"expected an entity of type {spec['type']!r} whose name contains "
            f"{spec['name_contains']!r}; got {[(e.type, e.name) for e in entities]}"
        )


def test_recall_fixture_extracts_every_must_contain_relationship():
    entities, relationships = _extract()
    entities_by_id = {e.id: e for e in entities}

    for spec in MUST_CONTAIN_RELATIONSHIPS:
        assert any(_relationship_matches(r, spec, entities_by_id) for r in relationships), (
            f"expected a {spec['type']!r} relationship from an entity containing "
            f"{spec['from_contains']!r} to one containing {spec['to_contains']!r}; "
            f"got {[(r.type, entities_by_id[r.from_id].name, entities_by_id[r.to_id].name) for r in relationships if r.from_id in entities_by_id and r.to_id in entities_by_id]}"
        )
