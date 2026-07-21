"""`DryRunSink` (spec §4.1's design test, ticket #9): collects the
`ExtractionResult` each `write()` receives instead of persisting it, and
never deletes anything. This is what lets `preview` (`app/api/preview.py`)
be implemented as a plain `Pipeline.run` call with no separate extraction
path — see `pipeline/core.py`'s module docstring for the design test this
sink exists to satisfy.
"""

from __future__ import annotations

from app.pipeline.sinks.base import SinkType
from app.pipeline.types import DeleteReport, ExtractionResult, SinkReport


class DryRunSink:
    sink_type: SinkType = "dryrun"

    def __init__(self) -> None:
        self.results: dict[str, ExtractionResult] = {}

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport:
        self.results[doc_id] = result
        return SinkReport(
            sink_type=self.sink_type,
            nodes_written=len(result.entities),
            relationships_written=len(result.relationships),
        )

    def delete_non_curated_for_doc(self, doc_id: str) -> DeleteReport:
        # A preview run never writes anything real, so there is nothing a
        # changed/removed doc could need cleaned up here either -- always
        # a no-op, matching Pipeline.run's `store=None` shape (no previous
        # hash means this is never even called during a preview run, but a
        # real SinkAdapter still needs to satisfy the protocol).
        return DeleteReport(deleted_count=0)
