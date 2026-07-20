"""The pipeline core (spec §4.1): [SourceAdapter] -> [Extraction] ->
[RuleEngine] -> [SinkAdapter(s)].

`Pipeline.run` is implemented starting with the incremental re-ingestion
ticket (hash-skip, curated immunity, orphan flagging) — this stub exists so
downstream modules can depend on a stable signature and type-check against
it before that behavior lands.

Design test (spec §4.1): the `preview` endpoint must be implementable as
`Pipeline.run` with a `DryRunSink` that collects instead of writes. If
preview ever needs a separate code path, this abstraction has failed and
must be fixed here, not worked around there.
"""

from __future__ import annotations

from typing import Any, Callable

from app.pipeline.sinks.base import SinkAdapter
from app.pipeline.sources.base import SourceAdapter
from app.pipeline.types import JobResult

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
        # 1. discover docs; compare content_hash against SQLite -> skip unchanged
        # 2. per changed doc: load -> extract -> apply rules
        # 3. per sink: delete_non_curated_for_doc(doc) -> write(result)
        # 4. diff discovered doc_ids against SQLite's previously-seen set;
        #    for each doc_id now missing, treat as removed: delete_non_curated_for_doc(doc)
        #    on every sink, then drop its SQLite hash-table row (ADR-0008)
        # 5. detect orphaned curated edges (§6.3) -> include in JobResult
        # 6. record hashes + per-doc status (including "removed") in SQLite
        raise NotImplementedError(
            "Pipeline.run lands with the incremental re-ingestion ticket"
        )
