"""End-to-end proof of the incremental re-ingestion contract (spec
§6.1-§6.3, issue #5) against both source adapters built so far. Runs the
real `Pipeline.run` + real `HashStore` (SQLite `:memory:`) + real Obsidian
and docx adapters against throwaway *copies* of the fixture vault/docx set
(never the committed fixtures themselves, so other tests' assumptions
about fixture content stay intact).

The one piece that can't be exercised against a real graph is Neo4j itself
(no live Neo4j in this environment — see test_neo4j_sink.py's header for
why, and docker-compose.yml for real integration testing). In its place,
`StatefulGraphSink` below implements `SinkAdapter`'s *same contract*
Neo4jSink does (curated-duplicate-skip on write, protected-node-delete +
orphan-flag on delete_non_curated_for_doc) as a small in-memory graph —
this proves `Pipeline.run`'s orchestration and the contract's semantics
end-to-end; Cypher-shape correctness for that same contract is covered
separately in test_neo4j_sink.py.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document as DocxDocument

from app.jobs.store import HashStore, connect
from app.pipeline.core import Pipeline
from app.pipeline.rules.schema import load_rule_file
from app.pipeline.sources.docx import DocxSourceAdapter
from app.pipeline.sources.obsidian import ObsidianSourceAdapter, load_config
from app.pipeline.types import DeleteReport, ExtractionResult, OrphanFlag, SinkReport, SourceDoc

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@dataclass
class StatefulGraphSink:
    """A minimal in-memory stand-in for Neo4jSink's contract (not its
    Cypher) -- see module docstring."""

    sink_type: str = "recording"
    nodes: dict[str, dict[str, object]] = field(default_factory=dict)
    rels: dict[tuple[str, str, str], dict[str, object]] = field(default_factory=dict)
    write_calls: list[str] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport:
        self.write_calls.append(doc_id)
        for entity in result.entities:
            existing = self.nodes.get(entity.id)
            if existing is not None and existing["origin"] == "curated":
                continue  # curated node immune, symmetric with relationships
            self.nodes[entity.id] = {"type": entity.type, "origin": entity.origin, "source_doc": doc_id}
        for rel in result.relationships:
            key = (rel.from_id, rel.to_id, rel.type)
            existing = self.rels.get(key)
            if existing is not None and existing["origin"] == "curated":
                continue  # curated wins -- duplicate not written (spec §6.2)
            self.rels[key] = {"origin": rel.origin, "source_doc": doc_id, "orphaned": False}
        return SinkReport(
            sink_type=self.sink_type,
            nodes_written=len(result.entities),
            relationships_written=len(result.relationships),
        )

    def delete_non_curated_for_doc(self, doc_id: str) -> DeleteReport:
        self.delete_calls.append(doc_id)

        for key in [k for k, v in self.rels.items() if v["source_doc"] == doc_id and v["origin"] != "curated"]:
            del self.rels[key]

        orphans: list[OrphanFlag] = []
        node_ids = [
            nid for nid, n in self.nodes.items() if n["source_doc"] == doc_id and n["origin"] != "curated"
        ]
        for nid in node_ids:
            attached_curated = [
                key for key in self.rels if nid in (key[0], key[1]) and self.rels[key]["origin"] == "curated"
            ]
            if attached_curated:
                for key in attached_curated:
                    self.rels[key]["orphaned"] = True
                    orphans.append(
                        OrphanFlag(
                            edge_id="|".join(key),
                            reason=f"endpoint '{nid}' sourced from doc '{doc_id}' no longer present",
                        )
                    )
                continue  # protect the node so the curated edge keeps an endpoint
            del self.nodes[nid]

        return DeleteReport(deleted_count=0, orphans=tuple(orphans))

    def insert_curated_relationship(self, from_id: str, to_id: str, rel_type: str) -> None:
        self.rels[(from_id, to_id, rel_type)] = {"origin": "curated", "source_doc": None, "orphaned": False}


def _copy_vault(tmp_path: Path) -> Path:
    dest = tmp_path / "vault"
    shutil.copytree(FIXTURES / "vault", dest)
    return dest


def _copy_docs(tmp_path: Path) -> Path:
    dest = tmp_path / "docs"
    shutil.copytree(FIXTURES / "docs", dest)
    return dest


def _run_obsidian(
    vault_path: Path, store: HashStore, sink: StatefulGraphSink | None = None
) -> tuple[object, StatefulGraphSink, ObsidianSourceAdapter]:
    # `sink` defaults to a fresh one, but callers doing a second run against
    # the same backing "graph" must pass the first run's sink back in --
    # a real Neo4j instance persists between runs; a freshly constructed
    # fake wouldn't, and would make "zero writes on rerun" assertions
    # vacuously true regardless of whether the skip logic actually worked.
    # `write_calls`/`delete_calls` reset per call -- they answer "what did
    # *this* job do", not a lifetime total; `nodes`/`rels` (the actual
    # graph state) persist across the reset, same as a real backing store.
    config = load_config(str(FIXTURES / "obsidian_config.yml"))
    source = ObsidianSourceAdapter(config)
    sink = sink if sink is not None else StatefulGraphSink()
    sink.write_calls = []
    sink.delete_calls = []
    job_result = Pipeline().run(
        source=source,
        source_path=str(vault_path),
        sinks=[sink],
        config=config,
        progress=lambda doc_id, fraction: None,
        store=store,
    )
    return job_result, sink, source


def _run_docx(
    docs_path: Path, store: HashStore, sink: StatefulGraphSink | None = None
) -> tuple[object, StatefulGraphSink]:
    rule_file = load_rule_file(str(FIXTURES / "br_requirements.yml"))
    source = DocxSourceAdapter(rule_file)
    sink = sink if sink is not None else StatefulGraphSink()
    sink.write_calls = []
    sink.delete_calls = []
    job_result = Pipeline().run(
        source=source,
        source_path=str(docs_path),
        sinks=[sink],
        config=rule_file,
        progress=lambda doc_id, fraction: None,
        store=store,
    )
    return job_result, sink


def _doc_id_for(docs: list[SourceDoc], name_fragment: str) -> str:
    return next(d.doc_id for d in docs if name_fragment in d.path)


# --- 1. Re-running with no fixture changes: all skipped, zero graph writes ---


def test_obsidian_rerun_with_no_changes_reports_all_skipped_and_writes_nothing(tmp_path):
    vault = _copy_vault(tmp_path)
    store = HashStore(connect(":memory:"))

    first, sink, _source = _run_obsidian(vault, store)
    assert all(s.outcome == "updated" for s in first.doc_statuses)

    second, sink2, _source2 = _run_obsidian(vault, store, sink=sink)

    assert all(s.outcome == "skipped" for s in second.doc_statuses)
    assert sink2.write_calls == []
    assert sink2.delete_calls == []


def test_docx_rerun_with_no_changes_reports_all_skipped_and_writes_nothing(tmp_path):
    docs = _copy_docs(tmp_path)
    store = HashStore(connect(":memory:"))

    first, sink = _run_docx(docs, store)
    assert all(s.outcome == "updated" for s in first.doc_statuses)

    second, sink2 = _run_docx(docs, store, sink=sink)

    assert all(s.outcome == "skipped" for s in second.doc_statuses)
    assert sink2.write_calls == []
    assert sink2.delete_calls == []


# --- 2. Modifying one fixture doc updates only that doc ---


def test_obsidian_modifying_one_note_updates_only_that_note(tmp_path):
    vault = _copy_vault(tmp_path)
    store = HashStore(connect(":memory:"))
    _first, sink, _source = _run_obsidian(vault, store)

    auth_service = vault / "Project" / "Architecture" / "Auth Service.md"
    auth_service.write_text(
        auth_service.read_text(encoding="utf-8") + "\nA newly added sentence changes the hash.\n",
        encoding="utf-8",
    )

    second, sink2, source = _run_obsidian(vault, store, sink=sink)
    changed_doc_id = _doc_id_for(source.discover(str(vault)), "Auth Service")

    outcomes = {s.doc_id: s.outcome for s in second.doc_statuses}
    assert outcomes[changed_doc_id] == "updated"
    assert all(o == "skipped" for doc_id, o in outcomes.items() if doc_id != changed_doc_id)
    assert sink2.write_calls == [changed_doc_id]
    assert sink2.delete_calls == [changed_doc_id]


def test_docx_modifying_one_doc_updates_only_that_doc(tmp_path):
    docs = _copy_docs(tmp_path)
    store = HashStore(connect(":memory:"))
    _first, sink = _run_docx(docs, store)

    target = docs / "plain_prose.docx"
    document = DocxDocument(str(target))
    document.add_paragraph("An additional paragraph changes the file's byte content.")
    document.save(str(target))

    source = DocxSourceAdapter(load_rule_file(str(FIXTURES / "br_requirements.yml")))
    changed_doc_id = _doc_id_for(source.discover(str(docs)), "plain_prose.docx")

    second, sink2 = _run_docx(docs, store, sink=sink)

    outcomes = {s.doc_id: s.outcome for s in second.doc_statuses}
    assert outcomes[changed_doc_id] == "updated"
    assert all(o == "skipped" for doc_id, o in outcomes.items() if doc_id != changed_doc_id)
    assert sink2.write_calls == [changed_doc_id]


# --- 3. Removing a fixture doc triggers delete_non_curated_for_doc and drops its hash row ---


def test_obsidian_removing_a_note_triggers_cleanup_and_drops_the_hash_row(tmp_path):
    vault = _copy_vault(tmp_path)
    store = HashStore(connect(":memory:"))
    _first, sink, _source = _run_obsidian(vault, store)

    source_for_lookup = ObsidianSourceAdapter(load_config(str(FIXTURES / "obsidian_config.yml")))
    removed_doc_id = _doc_id_for(source_for_lookup.discover(str(vault)), "Standup Notes")
    assert store.get_hash("obsidian", removed_doc_id) is not None  # sanity: was tracked

    (vault / "Project" / "Notes" / "Standup Notes.md").unlink()

    second, sink2, _source2 = _run_obsidian(vault, store, sink=sink)

    removed_statuses = [s for s in second.doc_statuses if s.doc_id == removed_doc_id]
    assert len(removed_statuses) == 1
    assert removed_statuses[0].outcome == "removed"
    assert removed_doc_id in sink2.delete_calls
    assert store.get_hash("obsidian", removed_doc_id) is None


# --- 4. A curated edge survives a re-run whose extraction would otherwise recreate a duplicate ---


def test_curated_relationship_survives_a_rerun_that_would_recreate_a_duplicate(tmp_path):
    vault = _copy_vault(tmp_path)
    store = HashStore(connect(":memory:"))
    _first, sink, source = _run_obsidian(vault, store)

    docs = source.discover(str(vault))
    fix_login_id = _doc_id_for(docs, "Fix Login Bug")
    auth_service_id = _doc_id_for(docs, "Auth Service")
    key = (fix_login_id, auth_service_id, "FIXES")
    assert sink.rels[key]["origin"] == "explicit"  # sanity: this is the edge extraction produces

    # A human promotes it to curated via the (not-yet-built) Graph page --
    # modeled directly here since that API is out of this ticket's scope.
    sink.rels[key]["origin"] = "curated"

    fix_login = vault / "Project" / "Tasks" / "Fix Login Bug.md"
    fix_login.write_text(
        fix_login.read_text(encoding="utf-8") + "\nAn extra line changes the hash but not the wikilinks.\n",
        encoding="utf-8",
    )

    second, sink2, _source2 = _run_obsidian(vault, store, sink=sink)

    outcome = next(s.outcome for s in second.doc_statuses if s.doc_id == fix_login_id)
    assert outcome == "updated"
    assert sink is sink2  # same StatefulGraphSink instance is reused across the two runs
    assert sink.rels[key]["origin"] == "curated"  # not clobbered back to 'explicit'


# --- 5. An endpoint disappearing flags the curated edge orphaned, without deleting it ---


def test_removing_a_curated_edges_endpoint_note_flags_it_orphaned_without_deleting_it(tmp_path):
    vault = _copy_vault(tmp_path)
    store = HashStore(connect(":memory:"))
    _first, sink, source = _run_obsidian(vault, store)

    docs = source.discover(str(vault))
    fix_login_id = _doc_id_for(docs, "Fix Login Bug")
    auth_service_id = _doc_id_for(docs, "Auth Service")
    key = (fix_login_id, auth_service_id, "FIXES")
    sink.rels[key]["origin"] = "curated"  # promote, as in the test above

    (vault / "Project" / "Architecture" / "Auth Service.md").unlink()

    second, sink2, _source2 = _run_obsidian(vault, store, sink=sink)

    assert sink is sink2
    assert len(second.orphans) == 1
    assert second.orphans[0].edge_id == "|".join(key)
    # Never auto-deleted: the curated edge and its (now content-less) node
    # both survive, merely flagged.
    assert key in sink.rels
    assert sink.rels[key]["origin"] == "curated"
    assert sink.rels[key]["orphaned"] is True
    assert auth_service_id in sink.nodes


# --- 6. Removing a wikilink from a note's body cleans up the corresponding explicit edge ---


def test_removing_a_wikilink_cleans_up_its_explicit_edge_on_reingest(tmp_path):
    vault = _copy_vault(tmp_path)
    store = HashStore(connect(":memory:"))
    _first, sink, source = _run_obsidian(vault, store)

    docs = source.discover(str(vault))
    fix_login_id = _doc_id_for(docs, "Fix Login Bug")
    auth_service_id = _doc_id_for(docs, "Auth Service")
    api_gateway_id = _doc_id_for(docs, "API Gateway")
    fixes_key = (fix_login_id, auth_service_id, "FIXES")
    other_key = (fix_login_id, api_gateway_id, "RELATES_TO")
    assert fixes_key in sink.rels
    assert other_key in sink.rels

    fix_login = vault / "Project" / "Tasks" / "Fix Login Bug.md"
    fix_login.write_text(
        "This bug was caused by [[API Gateway]] misrouting requests. "
        "See also [[Ghost Note]], which does not exist in this vault.\n",
        encoding="utf-8",
    )

    second, sink2, _source2 = _run_obsidian(vault, store, sink=sink)

    assert sink is sink2
    outcome = next(s.outcome for s in second.doc_statuses if s.doc_id == fix_login_id)
    assert outcome == "updated"
    assert fixes_key not in sink.rels  # the removed wikilink's edge is gone
    assert other_key in sink.rels  # the still-present wikilink's edge survives
