# 0010 — Human deletions are tombstoned, symmetric with curated-create immunity

## Status
Accepted

## Context
§6.2 makes a human's *create/retype* correction durable across re-ingestion: a curated edge always wins over a duplicate extracted one. §6.4 logs a *delete* correction to `corrections`, but as originally written nothing stops the next re-ingestion — which deletes and rewrites all of a doc's non-curated elements (ADR-0008, ADR-0009) — from silently recreating the exact edge the human just removed, if the source content still produces it. A correction that only sticks for creates and not for deletes is a real asymmetry, not an intentional design choice.

## Decision
A deletion via `DELETE /graph/relationships/{id}` records a durable suppression (keyed on doc_id + endpoints + relationship type, or equivalently treated as a live row in `corrections`) that the extracted/explicit rewrite step consults before recreating an edge. A suppressed edge is skipped on rewrite rather than recreated. This is symmetric with §6.2's curated-wins rule for creates.

## Consequences
- Phase 4's gate (§10) gains an assertion: a deleted edge stays deleted across a subsequent re-ingestion of the underlying doc, not just that a created curated edge survives one.
- If a user later wants a suppressed edge back, they'd need an explicit "un-suppress"/re-create action — not specified further here; out of scope for this round.
- The `corrections` table (or a related suppression store) becomes read from during ingestion, not just written to during canvas actions — a new coupling between the correction log and the pipeline that didn't exist in the original §6.4 framing.
