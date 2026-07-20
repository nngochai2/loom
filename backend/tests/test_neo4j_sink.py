"""Tests for the Neo4j sink against a fake driver/session double that
records the Cypher + parameters it's given. There's no live Neo4j available
in this environment (Docker Desktop's engine isn't running), so this
verifies the sink assembles correct queries and parameter rows rather than
actual graph-database semantics — real verification happens against the
docker-compose Neo4j once that's runnable.
"""

import importlib

from app.pipeline.sinks.neo4j import Neo4jSink
from app.pipeline.types import Entity, ExtractionResult, Relationship

_kg = importlib.import_module("kg-schema")


class FakeResult:
    def __init__(self, record=None):
        self._record = record

    def single(self):
        return self._record


class FakeSession:
    def __init__(self, run_results=None):
        self.queries: list[tuple[str, dict]] = []
        self._run_results = run_results or []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, cypher, **params):
        self.queries.append((cypher, params))
        if self._run_results:
            return self._run_results.pop(0)
        return FakeResult()


class FakeDriver:
    def __init__(self, run_results=None):
        self.session_obj = FakeSession(run_results)

    def session(self):
        return self.session_obj


def _entity(entity_id: str, entity_type: str, name: str, rule_id: str | None = "subfolder:architecture"):
    return Entity(
        id=entity_id,
        type=entity_type,
        name=name,
        origin="extracted",
        rule_id=rule_id,
        properties={"subfolder": "architecture"},
    )


def _relationship(from_id: str, to_id: str, rel_type: str = "DEPENDS_ON"):
    return Relationship(
        from_id=from_id,
        to_id=to_id,
        type=rel_type,
        origin="explicit",
        rule_id=None,
        properties={"alias": "Auth", "context": "depends on Auth"},
    )


def test_write_merges_entities_grouped_by_dynamic_label():
    driver = FakeDriver()
    sink = Neo4jSink(driver=driver)
    result = ExtractionResult(
        doc_id="doc1",
        content_hash="hash1",
        entities=(_entity("n1", "ARCHITECTURE", "Auth Service"),),
    )

    report = sink.write("doc1", result)

    queries = driver.session_obj.queries
    assert len(queries) == 1
    cypher, params = queries[0]
    assert "MERGE (n {id: row.id})" in cypher
    assert "n:ARCHITECTURE" in cypher
    row = params["rows"][0]
    assert row["id"] == "n1"
    assert row["name"] == "Auth Service"
    assert row["origin"] == "extracted"
    assert row["rule_id"] == "subfolder:architecture"
    assert row["source_doc"] == "doc1"
    assert row["content_hash"] == "hash1"
    assert row["properties"] == {"subfolder": "architecture"}
    assert report.nodes_written == 1


def test_write_merges_relationships_grouped_by_dynamic_type_via_bare_endpoint_merges():
    driver = FakeDriver()
    sink = Neo4jSink(driver=driver)
    result = ExtractionResult(
        doc_id="doc1",
        content_hash="hash1",
        relationships=(_relationship("n1", "n2"),),
    )

    report = sink.write("doc1", result)

    queries = driver.session_obj.queries
    assert len(queries) == 1
    cypher, params = queries[0]
    # Endpoints are merged bare (no label) so a forward reference to a node
    # not yet written by its own doc doesn't get silently dropped.
    assert "MERGE (src {id: row.from_id})" in cypher
    assert "MERGE (tgt {id: row.to_id})" in cypher
    assert "MERGE (src)-[r:DEPENDS_ON]->(tgt)" in cypher
    row = params["rows"][0]
    assert row["from_id"] == "n1"
    assert row["to_id"] == "n2"
    assert row["origin"] == "explicit"
    assert row["rule_id"] is None
    assert row["properties"] == {"alias": "Auth", "context": "depends on Auth"}
    assert report.relationships_written == 1


def test_write_stamps_schema_version_from_kg_schema():
    driver = FakeDriver()
    sink = Neo4jSink(driver=driver)
    result = ExtractionResult(doc_id="doc1", content_hash="hash1", entities=(_entity("n1", "TASK", "Fix bug"),))

    sink.write("doc1", result)

    _, params = driver.session_obj.queries[0]
    assert params["rows"][0]["schema_version"] == _kg.SCHEMA_VERSION


def test_write_groups_multiple_entity_types_into_separate_queries():
    driver = FakeDriver()
    sink = Neo4jSink(driver=driver)
    result = ExtractionResult(
        doc_id="doc1",
        content_hash="hash1",
        entities=(
            _entity("n1", "ARCHITECTURE", "A"),
            _entity("n2", "TASK", "B"),
        ),
    )

    sink.write("doc1", result)

    labels_touched = {cypher for cypher, _ in driver.session_obj.queries}
    assert len(labels_touched) == 2


def test_delete_non_curated_for_doc_excludes_curated_origin():
    driver = FakeDriver()
    sink = Neo4jSink(driver=driver)

    sink.delete_non_curated_for_doc("doc1")

    queries = driver.session_obj.queries
    assert any("origin" in cypher and "curated" in cypher for cypher, _ in queries)
    assert all(params.get("doc_id") == "doc1" for _, params in queries)


def test_delete_non_curated_uses_a_directed_relationship_match():
    # An undirected pattern (`]-()` instead of `]->()`) would match — and
    # delete — every relationship twice in real Neo4j, since every
    # relationship this sink writes is created directed.
    driver = FakeDriver()
    sink = Neo4jSink(driver=driver)

    sink.delete_non_curated_for_doc("doc1")

    rel_query = next(c for c, _ in driver.session_obj.queries if "DELETE r" in c)
    assert "]->()" in rel_query
    assert "]-()" not in rel_query


def test_delete_non_curated_node_query_protects_nodes_with_curated_relationships():
    # A plain DETACH DELETE would cascade onto any origin=curated edge still
    # attached to the node, violating curated immunity (spec §6.2).
    driver = FakeDriver()
    sink = Neo4jSink(driver=driver)

    sink.delete_non_curated_for_doc("doc1")

    node_query = next(c for c, _ in driver.session_obj.queries if "DETACH DELETE n" in c)
    assert "curated" in node_query
    assert "NOT EXISTS" in node_query


def test_entity_write_removes_stale_entity_labels_before_setting_the_new_one():
    # Without this, a note reclassified on re-ingestion (subfolder or
    # keyword-signal change) would accumulate both the old and new
    # entity-type labels instead of converging on one.
    driver = FakeDriver()
    sink = Neo4jSink(driver=driver)
    result = ExtractionResult(
        doc_id="doc1", content_hash="hash1", entities=(_entity("n1", "TASK", "Retitled note"),)
    )

    sink.write("doc1", result)

    cypher, _ = driver.session_obj.queries[0]
    assert "REMOVE n:" in cypher
    remove_line = next(line for line in cypher.splitlines() if line.startswith("REMOVE n:"))
    set_line = next(line for line in cypher.splitlines() if line.startswith("SET n:"))
    assert remove_line.index("REMOVE") < cypher.index(set_line)
    # every kg-schema entity type is stripped, not just the ones seen so far
    assert "TASK" in remove_line and "ARCHITECTURE" in remove_line
