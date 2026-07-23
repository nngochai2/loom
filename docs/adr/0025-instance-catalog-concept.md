# 0025 — Instance: a catalog concept over the shared graph, not a partition

## Status
Accepted

## Context
A "My instances" page was proposed to list "the created databases." Neo4j is a single shared graph with no per-source partitioning (§6.5, "one driver, one door"); Neo4j Community edition doesn't support multiple databases; there is no ChromaDB sink yet (Phase 5, issue #14, not built). Spec §2 explicitly rules out multi-tenancy and auth beyond a single shared instance. Resolved via `/grill-with-docs`.

## Decision
- **Instance** (see [CONTEXT.md](../../CONTEXT.md#instance)) is a new, Loom-native catalog concept: a named, saved recipe of **source type + source path + sink(s)**, owning a history of job runs against it. It is purely a UI/bookkeeping layer over the existing single shared Neo4j (and future single shared Chroma) — it does **not** reopen multi-tenancy or give Loom multiple physical databases.
- **Identity = source type + source path + sinks only.** The rule config used is not part of an instance's identity and can change freely across re-runs — editing rules and re-ingesting keeps the same instance and its history.
- **Duplicate instances are blocked.** Creating a second instance with the identical (source type, source path, sinks) tuple as an existing one is rejected.
- **No graph partitioning.** Nodes and edges are not tagged with an `instance_id`; `GET /graph/subgraph` and the Rules preview are unchanged. Graph data remains attributable only via existing `source_doc` provenance.
- **Every job now belongs to an instance.** `jobs.instance_id` is a `NOT NULL` foreign key — there is no more anonymous/ad-hoc run. If a user starts a run without naming an instance, one is auto-named from the source path (e.g. "Documents folder — q3-vendor-docs").
- **Deleting an instance is catalog-only.** It removes the instance's bookkeeping (name, config link, job history) but never touches the graph/vector data its runs wrote — Loom has no way to identify that subset of the shared graph to delete it. This mirrors how orphaned content already works (§6.3): data no longer attributed to anything active just sits there until a correction or re-ingestion addresses it.

## Consequences
- New `instances` table (`id`, `name`, `source_type`, `source_path`, `sinks`, `created_at`, `updated_at`) with a uniqueness constraint on `(source_type, source_path, sinks)`; `jobs` gains `instance_id NOT NULL REFERENCES instances(id)`.
- New API surface: `POST /instances`, `GET /instances`, `GET /instances/{id}`, `PATCH /instances/{id}` (rename), `DELETE /instances/{id}` (catalog-only); `POST /jobs` now takes `{instance_id, config_id}` instead of raw source/sink fields (spec §8 amended).
- `kg-schema` (§5), the Neo4j write path, and `GET /graph/subgraph` are untouched — this stays a small, additive feature, not a re-architecture.
- If per-instance graph filtering is ever wanted, that is a separate, much larger decision (schema versioning, backfill, API growth) and needs its own ADR — do not bolt it on here.
