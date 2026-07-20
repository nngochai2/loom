import pytest

from app.jobs.store import HashStore, connect
from app.pipeline.core import Pipeline
from app.pipeline.types import (
    DeleteReport,
    ExtractionResult,
    LoadedDoc,
    OrphanFlag,
    SinkReport,
    SourceDoc,
)


class ScriptedSource:
    source_type = "fake"

    def __init__(self, docs: list[SourceDoc]):
        self._docs = docs
        self.loaded: list[str] = []
        self.extracted: list[str] = []

    def discover(self, source_path: str) -> list[SourceDoc]:
        return self._docs

    def load(self, doc: SourceDoc) -> LoadedDoc:
        self.loaded.append(doc.doc_id)
        if doc.doc_id == "broken":
            raise ValueError("cannot read broken doc")
        return LoadedDoc(doc=doc, content="body")

    def extract(self, loaded: LoadedDoc, config: object) -> ExtractionResult:
        self.extracted.append(loaded.doc.doc_id)
        return ExtractionResult(doc_id=loaded.doc.doc_id, content_hash=loaded.doc.content_hash)


class RecordingSink:
    sink_type = "dryrun"

    def __init__(self, orphans_by_doc: dict[str, tuple[OrphanFlag, ...]] | None = None) -> None:
        self.writes: list[tuple[str, ExtractionResult]] = []
        self.deletes: list[str] = []
        self._orphans_by_doc = orphans_by_doc or {}

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport:
        self.writes.append((doc_id, result))
        return SinkReport(sink_type=self.sink_type)

    def delete_non_curated_for_doc(self, doc_id: str) -> DeleteReport:
        self.deletes.append(doc_id)
        return DeleteReport(deleted_count=0, orphans=self._orphans_by_doc.get(doc_id, ()))


def _doc(doc_id: str) -> SourceDoc:
    return SourceDoc(doc_id=doc_id, path=f"/vault/{doc_id}.md", content_hash=f"hash-{doc_id}")


@pytest.fixture()
def store() -> HashStore:
    return HashStore(connect(":memory:"))


def test_run_loads_extracts_and_writes_every_discovered_doc():
    docs = [_doc("a"), _doc("b")]
    source = ScriptedSource(docs)
    sink = RecordingSink()

    result = Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
    )

    assert source.loaded == ["a", "b"]
    assert source.extracted == ["a", "b"]
    assert [doc_id for doc_id, _ in sink.writes] == ["a", "b"]
    assert [s.outcome for s in result.doc_statuses] == ["updated", "updated"]


def test_run_writes_to_every_sink():
    docs = [_doc("a")]
    source = ScriptedSource(docs)
    sink1, sink2 = RecordingSink(), RecordingSink()

    Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink1, sink2],
        config=None,
        progress=lambda doc_id, fraction: None,
    )

    assert len(sink1.writes) == 1
    assert len(sink2.writes) == 1


def test_run_marks_a_failing_doc_as_failed_without_aborting_the_job():
    docs = [_doc("a"), _doc("broken"), _doc("b")]
    source = ScriptedSource(docs)
    sink = RecordingSink()

    result = Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
    )

    outcomes = {s.doc_id: s.outcome for s in result.doc_statuses}
    assert outcomes == {"a": "updated", "broken": "failed", "b": "updated"}
    assert "cannot read broken doc" in (
        next(s.error for s in result.doc_statuses if s.doc_id == "broken") or ""
    )
    # the failure shouldn't stop b from being processed and written
    assert [doc_id for doc_id, _ in sink.writes] == ["a", "b"]


def test_run_reports_progress_monotonically_to_completion():
    docs = [_doc("a"), _doc("b"), _doc("c")]
    source = ScriptedSource(docs)
    sink = RecordingSink()
    progress_calls: list[tuple[str, float]] = []

    Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: progress_calls.append((doc_id, fraction)),
    )

    assert progress_calls == [("a", pytest.approx(1 / 3)), ("b", pytest.approx(2 / 3)), ("c", pytest.approx(1.0))]


def test_run_with_no_discovered_docs_returns_empty_result():
    source = ScriptedSource([])
    sink = RecordingSink()

    result = Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
    )

    assert result.doc_statuses == []
    assert sink.writes == []


# --- Incremental re-ingestion (spec §6.1-§6.3), gated on `store` being
# passed. `store=None` (the tests above) keeps the original full-reingest
# behavior on purpose -- that's the shape `preview`/DryRunSink wants, since
# a preview must never perturb the real hash table. ---


def test_run_with_a_store_processes_a_never_before_seen_doc_as_updated_and_records_its_hash(store):
    source = ScriptedSource([_doc("a")])
    sink = RecordingSink()

    result = Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
        store=store,
    )

    assert [s.outcome for s in result.doc_statuses] == ["updated"]
    assert store.get_hash("fake", "a") == "hash-a"


def test_run_with_a_store_skips_an_unchanged_doc_with_zero_loads_and_zero_writes(store):
    store.set_hash("fake", "a", "hash-a", "t0")
    source = ScriptedSource([_doc("a")])
    sink = RecordingSink()

    result = Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
        store=store,
    )

    assert [s.outcome for s in result.doc_statuses] == ["skipped"]
    assert source.loaded == []
    assert source.extracted == []
    assert sink.writes == []
    assert sink.deletes == []


def test_run_with_a_store_only_reprocesses_the_doc_whose_hash_changed(store):
    store.set_hash("fake", "a", "hash-a", "t0")
    store.set_hash("fake", "b", "stale-hash-for-b", "t0")  # differs from ScriptedSource's "hash-b"
    source = ScriptedSource([_doc("a"), _doc("b")])
    sink = RecordingSink()

    result = Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
        store=store,
    )

    outcomes = {s.doc_id: s.outcome for s in result.doc_statuses}
    assert outcomes == {"a": "skipped", "b": "updated"}
    assert source.loaded == ["b"]
    assert [doc_id for doc_id, _ in sink.writes] == ["b"]
    assert store.get_hash("fake", "b") == "hash-b"


def test_run_with_a_store_deletes_non_curated_before_writing_a_changed_doc(store):
    store.set_hash("fake", "a", "stale-hash", "t0")
    source = ScriptedSource([_doc("a")])
    sink = RecordingSink()

    Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
        store=store,
    )

    assert sink.deletes == ["a"]
    assert [doc_id for doc_id, _ in sink.writes] == ["a"]


def test_run_with_a_store_does_not_delete_or_write_for_a_brand_new_doc(store):
    # There's nothing previously written for a doc store has never seen --
    # delete_non_curated_for_doc would be a wasted no-op call at best.
    source = ScriptedSource([_doc("a")])
    sink = RecordingSink()

    Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
        store=store,
    )

    assert sink.deletes == []
    assert [doc_id for doc_id, _ in sink.writes] == ["a"]


def test_run_with_a_store_treats_a_previously_seen_doc_missing_from_discovery_as_removed(store):
    store.set_hash("fake", "gone", "old-hash", "t0")
    source = ScriptedSource([])  # "gone" is no longer discovered
    sink = RecordingSink()

    result = Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
        store=store,
    )

    assert [s.outcome for s in result.doc_statuses] == ["removed"]
    assert sink.deletes == ["gone"]
    assert store.get_hash("fake", "gone") is None


def test_run_with_a_store_removal_cleanup_runs_against_every_sink(store):
    store.set_hash("fake", "gone", "old-hash", "t0")
    source = ScriptedSource([])
    sink1, sink2 = RecordingSink(), RecordingSink()

    Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink1, sink2],
        config=None,
        progress=lambda doc_id, fraction: None,
        store=store,
    )

    assert sink1.deletes == ["gone"]
    assert sink2.deletes == ["gone"]


def test_run_with_a_store_leaves_hashes_for_docs_that_are_still_present(store):
    store.set_hash("fake", "a", "hash-a", "t0")
    store.set_hash("fake", "gone", "old-hash", "t0")
    source = ScriptedSource([_doc("a")])
    sink = RecordingSink()

    Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
        store=store,
    )

    assert store.get_hash("fake", "a") == "hash-a"
    assert store.get_hash("fake", "gone") is None


def test_run_with_a_store_collects_orphan_flags_raised_by_a_sinks_delete(store):
    store.set_hash("fake", "a", "stale-hash", "t0")
    orphan = OrphanFlag(edge_id="4:abc:1", reason="endpoint gone")
    source = ScriptedSource([_doc("a")])
    sink = RecordingSink(orphans_by_doc={"a": (orphan,)})

    result = Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
        store=store,
    )

    assert result.orphans == [orphan]


def test_run_with_a_store_collects_orphan_flags_raised_by_doc_removal(store):
    store.set_hash("fake", "gone", "old-hash", "t0")
    orphan = OrphanFlag(edge_id="4:abc:2", reason="endpoint gone via removal")
    source = ScriptedSource([])
    sink = RecordingSink(orphans_by_doc={"gone": (orphan,)})

    result = Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
        store=store,
    )

    assert result.orphans == [orphan]


def test_run_with_a_store_does_not_persist_a_hash_when_a_doc_fails(store):
    source = ScriptedSource([_doc("broken")])
    sink = RecordingSink()

    result = Pipeline().run(
        source=source,
        source_path="./vault",
        sinks=[sink],
        config=None,
        progress=lambda doc_id, fraction: None,
        store=store,
    )

    assert [s.outcome for s in result.doc_statuses] == ["failed"]
    assert store.get_hash("fake", "broken") is None
    assert sink.deletes == []
    assert sink.writes == []
