# Loom — Domain Glossary

Terms sharpened during the [[grill-with-docs]] session against `docs/loom-spec.md` and NAA's real source (`D:\Cloned Projects\NAA`). See `docs/adr/` for the decisions behind these definitions.

## Rule file
A YAML file, source of truth on disk, defining how to extract structured items from one document source (e.g. docx table rows). Real shape (lifted from NAA's `parsing-rules/br_requirements.yml`, see ADR-0001):

- `id_pattern` / `id_format` — how to recognize and normalize an item's identifier.
- `title_from` — how to derive a title (e.g. `first_line`).
- `category_signals` — a list of `{id, name, pattern, flags}` regex signals; all matches are collected (not first-match-wins).
- `named_extractions` — a list of `{id, name, pattern, group, transform, filter, deduplicate, sort}` regex extractions producing `{name: [values]}`.
- `context` — flags controlling what non-item content gets collected as document-level context.

Not chunking parameters, not LLM prompts — Loom's extraction is deterministic pattern matching, not LLM-based (ADR-0001).

## Rule ID
The stable, generated `id` on each `category_signal`/`named_extraction` entry in a rule file — distinct from its human-editable `name`. Never changes when the rule is renamed; is the join key for `corrections.originating_rule_id` (spec §6.4). See ADR-0005.

## Entity type / Relationship type
Members of the fixed, versioned enum in `kg-schema/schema.json` (spec §5). Rule files map their output onto an existing type in this enum rather than declaring new Neo4j labels freely (ADR-0002). Starting vocabulary (ADR-0003):

- **Entity types (seeded from NAA's real Obsidian classifier):** `ARCHITECTURE`, `CONVENTION`, `TASK`, `BUSINESS_TERM`, `NOTE`, `TAG`. Docx entity types ship empty — the first docx rule file authored requires a schema-version bump to add its type.
- **Relationship types (full set lifted from NAA's wikilink-context keyword map):** `DEPENDS_ON`, `EXTENDS`, `USES`, `CONNECTS_TO`, `IMPLEMENTS`, `RELATES_TO`, `FIXES`, `RESOLVES`, `CAUSED_BY`, `FOLLOWS`, `VIOLATES`, `LINKS_TO` (default), `TAGGED_WITH` (tag-section links).

## Origin
Loom-native concept (not present in NAA at all — new for this project). One of `extracted` (pipeline-inferred), `explicit` (ground truth from the source itself, e.g. wikilinks), `curated` (human-edited via the graph canvas). See spec §5, §6.

## Golden-fixture parity test
The Phase 1 quality gate (ADR-0007): run NAA's current parser and Loom's ported adapter against the same fixture inputs and assert matching output. Replaces the spec's original (inapplicable) F1-benchmark language.

## Parent link (docx)
The generic, source-agnostic mechanism for structuring docx-sourced requirement nodes: an optional single `parent_node_id` per item. Deliberately *not* NAA's project-specific `Flow → UseCase → Document` hierarchy, which is not part of Loom's core (ADR-0006).

## Tombstone
A durable record of a human's deletion of an extracted or explicit edge (§6.4), consulted by `delete_non_curated_for_doc`'s rewrite step so re-ingestion doesn't silently recreate an edge a human removed. Symmetric with curated-create immunity (§6.2). See ADR-0010.

## delete_non_curated_for_doc
The (renamed, broadened) sink method that cleans up a document's prior contribution to the graph before rewriting it — covers both `origin: extracted` and `origin: explicit`, skips tombstoned edges, never touches `origin: curated`. Runs both when a doc's content changes and when a doc disappears from the source entirely (doc removal, ADR-0008). Formerly `delete_extracted_for_doc` in the original spec draft — see ADR-0009.

## Prose extraction
The opt-in, per-rule-file extraction path that runs a local LLM (Ollama) over a docx document's free-text content — the same text already collected by `context.include_paragraphs`/`include_non_br_tables` (see [[rule-file]]) but never mined for entities before. Runs *alongside* [[rule-file]]'s regex path, not instead of it; produces `origin: extracted` items the same as regex does. Narrows ADR-0001 rather than replacing it — regex remains the deterministic, required mechanism for tabular content; prose extraction is additive and disabled by default. See ADR-0018.

## Prose-extraction block
The `context.prose_extraction` entry in a docx rule file: `enabled` (default `false`), a stable generated `id` (same pattern as a `category_signal`/`named_extraction`'s `id`, see [[rule id]]), and `target_entity_types`/`target_relationship_types` — a user-chosen subset of `kg-schema`'s enum, not the full vocabulary. Its `id` is what gets stamped as `rule_id` on every entity/relationship [[prose extraction]] produces, so §6.4 correction-rate analytics groups it the same way as regex-derived rule_ids, no special-casing. See ADR-0018.

## Prompt version
A version marker for a prose-extraction block's prompt template, tracked per document alongside `content_hash` in SQLite. Bumping it (or changing the configured Ollama model) invalidates a document's prior LLM-derived extractions and forces re-extraction through the same `delete_non_curated_for_doc` → rewrite path used for an ordinary content change — even though the document's `content_hash` itself hasn't changed. Exists because [[prose extraction]] output isn't guaranteed reproducible across model/prompt changes the way regex output is. See ADR-0020.
