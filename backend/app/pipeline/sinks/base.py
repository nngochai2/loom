from typing import Literal, Protocol, runtime_checkable

from app.pipeline.types import ExtractionResult, SinkReport

SinkType = Literal["neo4j", "chroma", "dryrun"]


@runtime_checkable
class SinkAdapter(Protocol):
    sink_type: SinkType

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport: ...

    def delete_non_curated_for_doc(self, doc_id: str) -> int:
        """Remove origin='extracted' AND origin='explicit' elements sourced
        from doc_id, skipping any that are tombstoned as deleted (§6.4).
        Never touches origin='curated'. See §6.1, §6.2, ADR-0009, ADR-0010.
        """
        ...
