"""The pipeline core (spec §4.1): [SourceAdapter] -> [Extraction] ->
[RuleEngine] -> [SinkAdapter(s)].

Discover, then per doc load -> extract -> write to every sink, with
incremental re-ingestion layered on top when a `store` is supplied
(§6.1-§6.3): unchanged docs are hash-skipped with zero graph writes,
changed docs get `delete_non_curated_for_doc` before their fresh write,
docs SQLite previously saw but `discover()` no longer finds are treated as
removed the same way, and any orphan flags a sink's delete raises bubble
into `JobResult.orphans`. `store=None` keeps the original full-reingest
behavior — that's deliberate, not a fallback to patch over later: `preview`
must run through this exact method with a `DryRunSink` and no store, so a
preview run never perturbs the real hash table (see the design test below).

Design test (spec §4.1): the `preview` endpoint must be implementable as
`Pipeline.run` with a `DryRunSink` that collects instead of writes. If
preview ever needs a separate code path, this abstraction has failed and
must be fixed here, not worked around there.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from app.jobs.store import HashStore
from app.pipeline.sinks.base import SinkAdapter
from app.pipeline.sources.base import SourceAdapter
from app.pipeline.types import DocStatus, ExtractionVersion, JobResult

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
        store: HashStore | None = None,
        should_cancel: Callable[[], bool] | None = None,
        extraction_version: ExtractionVersion | None = None,
    ) -> JobResult:
        """`should_cancel` (spec §8) is polled at each doc boundary, not
        mid-doc: a cancelled run stops picking up new docs but never
        interrupts one already being written, so completed docs are never
        rolled back and the doc in flight either finishes clean or (if
        cancellation lands between docs) never starts.

        `extraction_version` (ADR-0020) is this run's LLM prompt/model
        fingerprint, or `None` for sources/configs with no such concept.
        Compared alongside `content_hash` in the skip check below: an
        unchanged document whose fingerprint has also drifted since the
        last run is *not* skipped, so a `prompt_version` bump or a model
        swap forces the same delete-then-rewrite path a content change
        already gets — Pipeline.run stays oblivious to what the fingerprint
        actually encodes, it only ever compares it for equality.

        A non-`None` `extraction.warning` (ADR-0022) — a degraded, partial
        extraction rather than a hard failure — surfaces on the doc's
        `DocStatus` as usual (`outcome` stays `"updated"`, regex/other
        output still wrote), but is written to `store` as if this run had
        no `extraction_version` at all: the next run's fingerprint
        comparison then finds a mismatch regardless of whether the
        document's content or the configured model/prompt_version changes
        again, forcing a retry instead of silently treating the degraded
        run as "successfully extracted at the current version" forever.
        """
        docs = source.discover(source_path)
        result = JobResult()
        total = len(docs)

        if store is not None:
            discovered_ids = {doc.doc_id for doc in docs}
            previously_seen = store.doc_ids_for_source(source.source_type)
            for doc_id in sorted(previously_seen - discovered_ids):
                if should_cancel is not None and should_cancel():
                    return result
                self._remove_doc(doc_id, sinks, store, source.source_type, result)

        for i, doc in enumerate(docs):
            if should_cancel is not None and should_cancel():
                break
            try:
                previous_hash = (
                    store.get_hash(source.source_type, doc.doc_id) if store is not None else None
                )
                previous_version = (
                    store.get_extraction_version(source.source_type, doc.doc_id)
                    if store is not None
                    else None
                )
                if (
                    store is not None
                    and previous_hash == doc.content_hash
                    and previous_version == extraction_version
                ):
                    result.doc_statuses.append(DocStatus(doc.doc_id, "skipped"))
                    progress(doc.doc_id, (i + 1) / total)
                    continue

                loaded = source.load(doc)
                extraction = source.extract(loaded, config)

                if store is not None and previous_hash is not None:
                    # A previously-seen doc whose hash just changed: clear
                    # what it contributed last time before rewriting it
                    # (§6.1). A brand-new doc has nothing to clear.
                    for sink in sinks:
                        report = sink.delete_non_curated_for_doc(doc.doc_id)
                        result.orphans.extend(report.orphans)

                for sink in sinks:
                    sink.write(doc.doc_id, extraction)

                if store is not None:
                    version_to_record = (
                        extraction_version if extraction.warning is None else None
                    )
                    store.set_hash(
                        source.source_type,
                        doc.doc_id,
                        doc.content_hash,
                        datetime.now(UTC).isoformat(),
                        prompt_version=(
                            version_to_record.prompt_version
                            if version_to_record is not None
                            else None
                        ),
                        model=version_to_record.model if version_to_record is not None else None,
                    )

                result.doc_statuses.append(
                    DocStatus(doc.doc_id, "updated", warning=extraction.warning)
                )
            except Exception as exc:
                result.doc_statuses.append(DocStatus(doc.doc_id, "failed", str(exc)))
            progress(doc.doc_id, (i + 1) / total)

        return result

    def _remove_doc(
        self,
        doc_id: str,
        sinks: list[SinkAdapter],
        store: HashStore,
        source_type: str,
        result: JobResult,
    ) -> None:
        """A doc SQLite previously tracked that `discover()` no longer finds
        (deleted from the vault/folder between runs) gets the same
        non-curated cleanup as a changed doc (ADR-0008), then its hash row
        is dropped so a later doc reusing the same id starts fresh."""
        for sink in sinks:
            report = sink.delete_non_curated_for_doc(doc_id)
            result.orphans.extend(report.orphans)
        store.delete_hash(source_type, doc_id)
        result.doc_statuses.append(DocStatus(doc_id, "removed"))
