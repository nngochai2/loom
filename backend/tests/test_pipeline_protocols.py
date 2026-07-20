import pytest

from app.pipeline.core import Pipeline
from app.pipeline.sinks.base import SinkAdapter, SinkType
from app.pipeline.sources.base import SourceAdapter
from app.pipeline.types import ExtractionResult, LoadedDoc, SinkReport, SourceDoc


class FakeSource:
    source_type = "fake"

    def discover(self, source_path: str) -> list[SourceDoc]:
        return [SourceDoc(doc_id="d1", path=source_path, content_hash="abc")]

    def load(self, doc: SourceDoc) -> LoadedDoc:
        return LoadedDoc(doc=doc, content="hello")


class FakeSink:
    sink_type: SinkType = "dryrun"

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport:
        return SinkReport(sink_type=self.sink_type)

    def delete_non_curated_for_doc(self, doc_id: str) -> int:
        return 0


def test_fake_source_satisfies_source_adapter_protocol():
    assert isinstance(FakeSource(), SourceAdapter)


def test_fake_sink_satisfies_sink_adapter_protocol():
    assert isinstance(FakeSink(), SinkAdapter)


def test_pipeline_run_matches_spec_signature_and_is_not_yet_implemented():
    pipeline = Pipeline()
    with pytest.raises(NotImplementedError):
        pipeline.run(
            source=FakeSource(),
            source_path="./fixtures/vault",
            sinks=[FakeSink()],
            config=None,
            progress=lambda doc_id, fraction: None,
        )
