# 0020 — Model/prompt version tracked alongside content_hash; a bump forces re-extraction

## Status
Accepted

## Context
§6.1's "incremental by default" invariant skips a document when its `content_hash` is unchanged from the last run — correct for deterministic regex extraction, where identical input always yields identical output. ADR-0018's LLM path breaks that assumption: the *document* can be byte-identical while the *output* changes anyway, because the configured model was upgraded or the prose-extraction prompt template was edited. Left untracked, stale LLM-derived extractions from an old model/prompt would sit silently in the graph next to freshly-produced ones from docs that happened to get touched for unrelated reasons — inconsistent and hard to trust.

## Decision
Track a `prompt_version` (bumped whenever the prose-extraction prompt template changes) and the configured Ollama model name as part of what's compared per document, alongside `content_hash`. A change to either invalidates prior LLM-derived extractions for documents using that rule file's `prose_extraction` block, exactly the same way a content change does: `delete_non_curated_for_doc` then re-write. Curated edges remain immune (§6.2); tombstones remain honored (§6.4, ADR-0010) — this reuses the existing invariant machinery unmodified; it only adds a second trigger condition beside `content_hash`.

## Consequences
- SQLite's per-doc hash-tracking table (§3) needs to additionally record which `prompt_version`/model a document's current LLM-derived extractions came from — extends the existing table, not a new one.
- The existing "from-scratch rebuild via clearing the SQLite hash table" escape hatch (§6.1) still works unmodified as the blunt-force option covering this case too.
- This is a re-run *trigger* mechanism, not a claim that the LLM path is now deterministic — the same content+model+prompt combination can still produce slightly different output between runs. Quality/regression protection is ADR-0021's test gate, not this versioning mechanism.
