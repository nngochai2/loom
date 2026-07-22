# 0018 — Opt-in LLM-based prose extraction, alongside (not instead of) regex

## Status
Accepted

## Context
ADR-0001 established deterministic, regex-only extraction and explicitly flagged its own boundary: "If genuine LLM-based extraction is wanted later, it is new scope requiring its own spec change, not an extension of 'lift, don't rewrite.'" That moment has arrived — product scope was grilled and confirmed (2026-07-21) to explicitly include free-text prose content, not just tabular content, for general (non-personal-use) audiences whose source documents don't all look like `br_requirements.yml`-shaped tables.

Real docx documents already mix table rows with prose (intro paragraphs, narrative notes around the tables). `DocxSourceAdapter.load()` (`backend/app/pipeline/sources/docx.py`) already collects this text into `LoadedDoc.content` via the existing `context.include_paragraphs`/`include_non_br_tables` rule-file flags — but `extract()` only ever reads `metadata["rows"]` for the regex engine. `content` is collected today and never mined for entities/relationships. Regex cannot meaningfully extract structured entities from unstructured prose; that's a different kind of task, requiring actual language understanding.

## Decision
Add an **opt-in, per-rule-file** LLM-based extraction path that runs *alongside* the existing regex path within the same docx document — not a new source adapter, not a replacement.

- The rule file's `context` block gains a new sub-block (e.g. `prose_extraction`) with:
  - `enabled: bool`, default `false`. Existing rule files are unaffected until a user opts in.
  - A stable, generated `id`, following the same pattern ADR-0005 established for `category_signals`/`named_extractions`. This id becomes the `rule_id` stamped on every entity/relationship this path produces, so §6.4 correction-rate analytics groups LLM-derived items the same way as regex-derived ones, with no special-casing.
  - `target_entity_types` / `target_relationship_types` — user-configured subsets of `kg-schema`'s enum (ADR-0002/0003). The LLM is scoped to a specific target list per rule file, not given the full vocabulary and asked to "find anything" — mirrors how regex rules are already narrow and specific.
- `DocxSourceAdapter.extract()` runs the LLM extractor over `loaded.content` when the block is enabled, and merges its output into the same `ExtractionResult` as the regex path's output. Both produce `origin: extracted` items — the schema contract (§5, §6) doesn't need to change for this.

This narrows ADR-0001, it does not overturn it: regex remains the required, deterministic mechanism for tabular content. The LLM path is additive, and only touches documents/rule-files that explicitly opt in.

## Consequences
- `pipeline/rules/schema.py`'s JSON Schema needs a new `context.prose_extraction` sub-schema (implementation-time work, not part of this scoping decision).
- ADR-0007's golden-fixture parity gate is unaffected — it still governs the regex path only. See ADR-0021 for the LLM path's own, differently-shaped test gate.
- See ADR-0019 (how the LLM is served), ADR-0020 (re-extraction triggering), ADR-0021 (testing), ADR-0022 (failure handling) for how this path actually behaves at runtime.
- `loom-spec.md` needed updating in several places that flatly stated extraction is non-LLM (see the same commit/session) — those statements now describe the regex path specifically, not the whole pipeline.
