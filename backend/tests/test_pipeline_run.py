import pytest

from app.pipeline.core import Pipeline
from app.pipeline.types import (
    ExtractionResult,
    LoadedDoc,
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

    def __init__(self) -> None:
        self.writes: list[tuple[str, ExtractionResult]] = []

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport:
        self.writes.append((doc_id, result))
        return SinkReport(sink_type=self.sink_type)

    def delete_non_curated_for_doc(self, doc_id: str) -> int:
        return 0


def _doc(doc_id: str) -> SourceDoc:
    return SourceDoc(doc_id=doc_id, path=f"/vault/{doc_id}.md", content_hash=f"hash-{doc_id}")


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
