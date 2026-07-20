"""The pipeline core (spec §4.1): [SourceAdapter] -> [Extraction] ->
[RuleEngine] -> [SinkAdapter(s)].

This is the first-pass implementation: discover, then per doc load ->
extract -> write to every sink. Hash-skip, curated immunity, orphan
flagging, and doc-removal cleanup (§6.1-§6.3) land with the incremental
re-ingestion ticket, extending this same method.

Design test (spec §4.1): the `preview` endpoint must be implementable as
`Pipeline.run` with a `DryRunSink` that collects instead of writes. If
preview ever needs a separate code path, this abstraction has failed and
must be fixed here, not worked around there.
"""

from __future__ import annotations

from typing import Any, Callable

from app.pipeline.sinks.base import SinkAdapter
from app.pipeline.sources.base import SourceAdapter
from app.pipeline.types import DocStatus, JobResult

# Reports (doc_id, progress fraction 0.0-1.0) as a job proceeds.
ProgressCallback = Callable[[str, float], None]


class Pipeline:
    def run(
        self,
        source: SourceAdapter,
        source_path: str,
        sinks: list[SinkAdapter],
        config: Any,
        progress: ProgressCallback,
    ) -> JobResult:
        docs = source.discover(source_path)
        result = JobResult()
        total = len(docs)

        for i, doc in enumerate(docs):
            try:
                loaded = source.load(doc)
                extraction = source.extract(loaded, config)
                for sink in sinks:
                    sink.write(doc.doc_id, extraction)
                result.doc_statuses.append(DocStatus(doc.doc_id, "updated"))
            except Exception as exc:
                result.doc_statuses.append(DocStatus(doc.doc_id, "failed", str(exc)))
            progress(doc.doc_id, (i + 1) / total)

        return result
