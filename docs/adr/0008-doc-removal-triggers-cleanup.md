# 0008 — A document removed from the source triggers the same cleanup as a changed document

## Status
Accepted

## Context
Spec §6.1's incremental logic only covers documents that changed content_hash: `delete_extracted_for_doc` then `write`. §4.1's `Pipeline.run` pseudocode says nothing about a document that `discover()` no longer finds at all (deleted from the vault/folder between runs). NAA itself has no answer to copy here — it never diffs against previous state; every run just MERGEs whatever it currently finds, so a removed source file's graph content simply never gets cleaned up in NAA today (a real, pre-existing gap, not something to preserve).

## Decision
`Pipeline.run` compares the newly discovered doc set against SQLite's previously-seen doc set. Any previously-seen `doc_id` missing from the current discovery is treated as deleted: run `delete_extracted_for_doc` (and explicit-edge cleanup, ADR-0009) against it, then remove its row from the SQLite hash table. Curated edges left dangling by this cleanup are orphan-flagged per §6.3, exactly as in the changed-doc case.

## Consequences
- `JobResult` needs a `removed` doc-status alongside `skipped | updated | failed`, so the Ingest results table (§9) can surface it.
- This is genuinely new behavior beyond what NAA does — not a lift, a fix.
