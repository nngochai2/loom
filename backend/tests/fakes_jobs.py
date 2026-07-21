"""Fake `SourceAdapter`/`SinkAdapter` shapes shared by `test_jobs_runner.py`
and `test_api_jobs.py` — both drive a `JobRunner`/the Jobs API through a
fake registry (the same injectable-registry seam `cli.run_ingest` and
`JobRunner` leave at their defaults) rather than a live Neo4j.
"""

from __future__ import annotations

import threading

from app.pipeline.types import DeleteReport, ExtractionResult, LoadedDoc, SinkReport, SourceDoc


def doc(doc_id: str) -> SourceDoc:
    return SourceDoc(doc_id=doc_id, path=f"/vault/{doc_id}.md", content_hash=f"hash-{doc_id}")


class ScriptedSource:
    source_type = "fake"

    def __init__(self, docs: list[SourceDoc]) -> None:
        self._docs = docs
        self.loaded: list[str] = []

    def discover(self, source_path: str) -> list[SourceDoc]:
        return self._docs

    def load(self, doc: SourceDoc) -> LoadedDoc:
        self.loaded.append(doc.doc_id)
        return LoadedDoc(doc=doc, content="body")

    def extract(self, loaded: LoadedDoc, config: object) -> ExtractionResult:
        return ExtractionResult(doc_id=loaded.doc.doc_id, content_hash=loaded.doc.content_hash)


class ControllableSource(ScriptedSource):
    """Like ScriptedSource, but `load()` blocks until the test releases it —
    lets a test observe/cancel a job mid-run deterministically instead of
    racing real timing."""

    def __init__(self, docs: list[SourceDoc]) -> None:
        super().__init__(docs)
        self.started = threading.Event()
        self.release = threading.Event()

    def load(self, doc: SourceDoc) -> LoadedDoc:
        self.loaded.append(doc.doc_id)
        self.started.set()
        assert self.release.wait(timeout=5), "test never released the blocked doc load"
        return LoadedDoc(doc=doc, content="body")


class RecordingSink:
    sink_type = "dryrun"

    def __init__(self) -> None:
        self.writes: list[tuple[str, ExtractionResult]] = []
        self.deletes: list[str] = []

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport:
        self.writes.append((doc_id, result))
        return SinkReport(sink_type=self.sink_type)

    def delete_non_curated_for_doc(self, doc_id: str) -> DeleteReport:
        self.deletes.append(doc_id)
        return DeleteReport(deleted_count=0)
