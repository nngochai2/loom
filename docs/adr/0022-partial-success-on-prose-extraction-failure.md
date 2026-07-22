# 0022 — Partial success on prose-extraction failure: regex output still writes

## Status
Accepted

## Context
A single docx document can now produce output from two independent mechanisms (ADR-0018): regex over table rows, and an LLM over prose content. Ollama being slow, unreachable, or timing out is a plausible, non-catastrophic failure mode — distinct from a config validation error — that specifically affects only the LLM half of a document's extraction, not the regex half.

## Decision
If prose extraction fails for a document (LLM unreachable, timeout, malformed/unusable response) but regex extraction for that same document's table rows succeeded, the document is still written with its regex-derived entities/relationships. Prose extraction is skipped for that document on that run, and the document's result surfaces a **warning** — reusing the same expandable-detail pattern already planned for orphan warnings in the Ingest results table (spec §9) — rather than marking the whole document `failed`. The rest of the job continues processing subsequent documents normally; one document's LLM hiccup doesn't halt the batch.

## Consequences
- `DocStatus`/`JobResult` (`backend/app/pipeline/types.py`) need a way to carry a per-document warning distinct from the existing `failed` outcome and from `OrphanFlag` — a new warning-list field, shape decided at implementation time.
- A document whose prose extraction failed must **not** be recorded as "successfully extracted at prompt_version X" (ADR-0020) — a subsequent run should retry prose extraction for it, not silently treat it as up to date forever because its content_hash didn't change.
- This covers a mid-job runtime failure, not Ollama being down at backend startup — that's a deployment/health-check concern (ADR-0019), handled separately from per-job outcomes.
