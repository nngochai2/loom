from typing import Literal, Protocol, runtime_checkable

from app.pipeline.types import DeleteReport, ExtractionResult, SinkReport

SinkType = Literal["neo4j", "chroma", "dryrun"]


@runtime_checkable
class SinkAdapter(Protocol):
    sink_type: SinkType

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport: ...

    def delete_non_curated_for_doc(self, doc_id: str) -> DeleteReport:
        """Remove origin='extracted' AND origin='explicit' elements sourced
        from doc_id, skipping any that are tombstoned as deleted (§6.4).
        Never touches origin='curated'. See §6.1, §6.2, ADR-0009, ADR-0010.

        Returns both how much was deleted and any curated edges left
        resting on content that no longer exists as a result (§6.3) — a
        curated edge is never itself deleted, only flagged.
        """
        ...
