# Loom — Project Specification

> **Audience:** Claude Code (implementation agent) and human reviewers.
> **Status:** v1.0 — approved for implementation.
> **Project name:** Loom — weaving scattered documents into a connected knowledge fabric.

---

## 1. Context and motivation

We have an existing internal tool, **NAA**, which grew to cover document parsing (Obsidian, docx), knowledge-graph ingestion, *and* MCP server hosting/configuration. It has become scattered: the write path (batch parsing, human-in-the-loop config) and the read path (long-running MCP services consumed by agents) were bundled into one app with one UI, and neither is served well.

**Loom is the write path, extracted and rebuilt as a compact, focused application.** NAA will be slimmed down to the read path (MCP server only) in a separate effort — out of scope here.

The two systems never communicate directly. **The database is the contract**: Loom writes to Neo4j (and a vector store); the MCP server reads from them. The shared graph schema is the only coupling point and is treated as a versioned artifact (see §5).

### Existing assets to reuse (lift, do not rewrite)

> **Corrected after grilling against the actual NAA source** (`D:\Cloned Projects\NAA`) — see [ADR-0001](adr/0001-lift-deterministic-extraction-not-llm.md). The extraction logic in NAA is **deterministic, regex/rule-based** — not LLM-based. There is no Obsidian CLI anywhere in NAA, no LlamaIndex usage in `pipeline/` or `webapp/`, and no benchmark harness matching an F1/KGGen/MINE-1 methodology. The mess in NAA is in the glue and the shell, not the core — that part of the original claim holds; the *mechanism* claim didn't.

- **Obsidian ingestion** — `NAA/pipeline/src/parser.py` + `config.py`: hand-rolled regex over raw `.md` text (wikilinks, a custom non-YAML header). Lift this as-is. Vault-specific classification config (folder→type map, keyword signals, relationship-inference keywords, included folders) moves into per-config YAML rather than staying hardcoded — see [ADR-0004](adr/0004-classification-rules-in-yaml-config.md).
- **docx extraction** — `NAA/webapp/src/docx_generic_parser.py` (`DocxRuleParser`): reads a YAML rule file and applies regex patterns to table rows. Lift this. Only the generic parent-link path is lifted, not NAA's project-specific `Flow → UseCase → Document` hierarchy or its unused `SqlView`/`OraclePackage` machinery — see [ADR-0006](adr/0006-generic-docx-parent-link-only.md).
- **Neo4j schema mapping** — NAA's real vocabulary seeds Loom's fixed, versioned entity/relationship enum; it is not lifted wholesale (docx's `BR` type is project-specific and excluded). See [ADR-0002](adr/0002-fixed-versioned-schema-not-dynamic-labels.md) and [ADR-0003](adr/0003-schema-vocabulary-source.md).

Source paths (resolved — no longer HUMAN INPUT REQUIRED):
- Obsidian: `NAA/pipeline/src/parser.py`, `NAA/pipeline/src/models.py`, `NAA/pipeline/src/config.py`
- docx: `NAA/webapp/src/docx_generic_parser.py`, example rule file `NAA/parsing-rules/br_requirements.yml`
- Neo4j writes: `NAA/pipeline/src/graph.py` (only `upsert_notes`/`upsert_relationships`/generic `upsert_requirements` are live — SQL/Oracle-specific methods are dead code, not lifted)

Treat these as read-only references; copy code into Loom's structure, adapting to the adapter protocols in §4.

---

## 2. Goals and non-goals

### Goals (the entire product)

Three core features — which are **one pipeline** with pluggable sources and sinks:

1. Parse documents (docx first; xlsx/pptx later) into a **graph database** (Neo4j).
2. Parse an **Obsidian vault** into the graph database.
3. Parse documents into a **vector database** (ChromaDB first).

Two side features:

4. **User-friendly parsing-rule configuration** — a form-based editor generated from a JSON Schema of the YAML config format, with **live extraction preview** against a sample document. YAML remains the source of truth on disk.
5. **Graph correction canvas** — an NVL-based view where a user can create, retype, and delete relationships. Framed strictly as a *correction loop for extraction errors*, not a general graph editor.

### Non-goals (hard boundaries — do not implement, do not scaffold "for later")

- ❌ Document storage, versioning, folders, or file permissions. Loom *points at* documents; it never becomes a document manager.
- ❌ MCP server hosting, configuration, or management of any kind. If a "what do agents see" feature is ever wanted, it is a read-only query preview — and it is not in this spec.
- ❌ Multi-tenancy, auth beyond a single shared instance, SharePoint integration.
- ❌ A visual drag-and-drop rule builder. The form editor + preview (§7) is the ceiling for config UX in v1.
- ❌ General node CRUD in the graph canvas. v1 correction scope is **relationships only** (create/retype/delete). Node label/property editing is deferred.

---

## 3. Tech stack (fixed — do not substitute)

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.11+, FastAPI | |
| Graph DB | Neo4j Community Edition (self-hosted), `neo4j` bolt driver | Driver lives in exactly one module (§4) |
| Vector DB | ChromaDB | Sink added in Phase 5 only |
| Operational store | SQLite | Jobs, hashes, correction log — never in Neo4j |
| Extraction | Stdlib `re` (regex) | Deterministic, rule-driven — see [ADR-0001](adr/0001-lift-deterministic-extraction-not-llm.md) |
| Frontend | React + Vite + TypeScript + Tailwind | |
| Graph rendering | `@neo4j-nvl/react` (fall back to `@neo4j-nvl/base` if the wrapper is limiting) | Set `disableTelemetry: true` in nvlOptions |
| Rule form | react-jsonschema-form (or equivalent JSON-Schema-driven form lib) | |

**Security invariant:** the bolt driver and all DB credentials live exclusively in the backend. The browser never connects to Neo4j directly.

---

## 4. Architecture

### 4.1 Pipeline (the core abstraction)

```
[SourceAdapter] → [Extraction] → [RuleEngine] → [SinkAdapter(s)]
```

Everything is one pipeline. Obsidian vs docx are source adapters; Neo4j vs ChromaDB are sink adapters; "parse to both" is two sinks on one extraction output.

```python
# pipeline/sources/base.py
class SourceAdapter(Protocol):
    source_type: str  # "obsidian" | "docx" | ...

    def discover(self, source_path: str) -> list[SourceDoc]:
        """Enumerate documents. SourceDoc carries path, doc_id, content_hash."""

    def load(self, doc: SourceDoc) -> LoadedDoc:
        """Read one document into text + structural metadata.
        For Obsidian: wikilinks are emitted as explicit edges here,
        tagged origin='explicit' — they bypass extraction inference."""
```

```python
# pipeline/sinks/base.py
class SinkAdapter(Protocol):
    sink_type: str  # "neo4j" | "chroma" | "dryrun"

    def write(self, doc_id: str, result: ExtractionResult) -> SinkReport: ...
    def delete_non_curated_for_doc(self, doc_id: str) -> int:
        """Remove origin='extracted' AND origin='explicit' elements sourced
        from doc_id, skipping any that are tombstoned as deleted (§6.4).
        Never touches origin='curated'. See §6.1, §6.2, ADR-0009, ADR-0010."""
```

```python
# pipeline/core.py
class Pipeline:
    def run(self, source: SourceAdapter, source_path: str,
            sinks: list[SinkAdapter], config: ParsingConfig,
            progress: ProgressCallback) -> JobResult:
        # 1. discover docs; compare content_hash against SQLite → skip unchanged
        # 2. per changed doc: load → extract → apply rules
        # 3. per sink: delete_non_curated_for_doc(doc) → write(result)
        # 4. diff discovered doc_ids against SQLite's previously-seen set;
        #    for each doc_id now missing, treat as removed: delete_non_curated_for_doc(doc)
        #    on every sink, then drop its SQLite hash-table row (ADR-0008)
        # 5. detect orphaned curated edges (§6.3) → include in JobResult
        # 6. record hashes + per-doc status (including "removed") in SQLite
```

**Design test:** the `preview` endpoint (§8) must be implementable as `Pipeline.run` with a `DryRunSink` that collects instead of writes. If preview needs a separate code path, the abstraction has failed — fix the abstraction.

### 4.2 Repository layout

```
loom/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── jobs.py
│   │   │   ├── configs.py
│   │   │   └── graph.py
│   │   ├── pipeline/
│   │   │   ├── core.py
│   │   │   ├── types.py            # SourceDoc, LoadedDoc, ExtractionResult, JobResult
│   │   │   ├── sources/{base,obsidian,docx}.py
│   │   │   ├── extraction/{chunking,entities}.py
│   │   │   ├── rules/{engine,schema}.py
│   │   │   └── sinks/{base,neo4j,vector,dryrun}.py
│   │   ├── jobs/{runner,store}.py
│   │   └── db/neo4j_client.py      # the ONLY module importing the bolt driver
│   ├── cli.py                      # Phase 1 entry point: run pipeline without API/UI
│   └── tests/
│       └── fixtures/               # mini Obsidian vault + sample docx files
├── frontend/
│   └── src/
│       ├── pages/{Ingest,Rules,Graph}.tsx
│       ├── components/
│       └── api/client.ts
└── kg-schema/
    ├── schema.json                 # source of truth (§5)
    ├── __init__.py                 # Python constants derived from schema.json
    └── VERSION
```

`kg-schema/` is a folder now, designed to be extracted into a standalone package the day NAA's MCP server needs to import it. Write it with that future in mind (no imports from `app/`).

---

## 5. The schema contract (`kg-schema`)

`schema.json` defines, versioned. Rule files map their output onto an entity/relationship type already in this enum — they never declare a Neo4j label directly (see [ADR-0002](adr/0002-fixed-versioned-schema-not-dynamic-labels.md)):

- **Entity types (seeded from NAA's real Obsidian classifier — see [ADR-0003](adr/0003-schema-vocabulary-source.md)):** `ARCHITECTURE`, `CONVENTION`, `TASK`, `BUSINESS_TERM`, `NOTE`, `TAG`.
  Docx entity types ship **empty**. The first docx rule file authored (BR-shaped or otherwise) requires its author to add its entity type via a schema-version bump — no generic fallback type, and NAA's `BR` is deliberately not carried over (it's specific to one project).
- **Relationship types (full set lifted from NAA's wikilink-context keyword map):** `DEPENDS_ON`, `EXTENDS`, `USES`, `CONNECTS_TO`, `IMPLEMENTS`, `RELATES_TO`, `FIXES`, `RESOLVES`, `CAUSED_BY`, `FOLLOWS`, `VIOLATES`, `LINKS_TO` (default when no keyword matches), `TAGGED_WITH` (tag-section wikilinks).
- **Mandatory properties on every node and relationship written by Loom:**

| Property | Type | Values / notes |
|---|---|---|
| `origin` | string | `extracted` (inferred by pipeline) · `explicit` (ground truth from source, e.g. wikilinks) · `curated` (created/edited by a human in the canvas) |
| `source_doc` | string | doc_id of originating document; absent on `curated` |
| `content_hash` | string | hash of source doc at write time; absent on `curated` |
| `rule_id` | string | id of the parsing rule that produced it; absent on `explicit`/`curated` |
| `schema_version` | string | value of `kg-schema/VERSION` at write time |
| `created_at` / `updated_at` | ISO 8601 | |

Adding new entity/relationship types later is a schema-version bump. The Python constants module is generated from (or validated against) `schema.json` in CI — the JSON is authoritative.

---

## 6. Behavioral invariants (the rules that keep this sane)

### 6.1 Incremental by default
Every job computes `content_hash` per discovered document and compares against SQLite. Unchanged docs are **skipped** and reported as such. Changed docs are re-processed: `delete_non_curated_for_doc` then `write`. A document removed from the source entirely (no longer found by `discover()`) is treated the same way — `delete_non_curated_for_doc` runs against it and its SQLite hash-table row is dropped (see ADR-0008). There is no separate "full re-ingest" button in v1; deleting the SQLite hash table is the escape hatch for a from-scratch rebuild.

`delete_non_curated_for_doc` removes both `origin: extracted` **and** `origin: explicit` elements sourced from the doc (e.g. a wikilink removed from a note's body is cleaned up on re-ingestion, not left stale forever — see ADR-0009). `origin: curated` is never touched, and any edge tombstoned per §6.4 is skipped on rewrite rather than recreated.

### 6.2 Curated is immune
Elements with `origin: curated` survive every re-ingestion, unconditionally. If a re-parse produces an extracted or explicit edge duplicating a curated edge (same endpoints + type), **the curated edge wins** and the duplicate is not written. Correspondingly, `POST /graph/relationships` (§8) MERGEs on `(from_id, to_id, type)`: creating a relationship over an already-existing edge of that type **promotes** it to curated rather than creating a parallel duplicate (see ADR-0011).

### 6.3 Orphans are flagged, never auto-deleted
If re-ingestion (including a doc-removal cleanup, §6.1) removes a node that a curated edge depends on, do **not** delete the curated edge and do not silently keep a dangling reference. Mark the edge `orphaned: true`, surface it in the `JobResult`, and let the human resolve it in the Graph page. Automation never overrules a person silently.

### 6.4 Corrections are logged, and deletions are tombstoned
Every canvas action writes to the SQLite `corrections` table **before** the graph write:

```sql
corrections(id, timestamp, action /* create|retype|delete */,
            rel_type, from_node_id, to_node_id,
            originating_rule_id /* nullable — the rule that created the edge being corrected */)
```

Purpose: per-rule correction-rate analytics later ("rule X has a 30% correction rate"). No analytics UI in v1 — just capture the data.

A `delete` correction is durable across re-ingestion: it acts as a **tombstone** that `delete_non_curated_for_doc`'s rewrite step consults, so a human's deletion of an extracted or explicit edge isn't silently undone the next time its source doc is reprocessed and would otherwise still produce it. This is symmetric with §6.2's curated-wins rule for creates/retypes (see ADR-0010). Phase 4's gate (§10) includes asserting that a deleted edge stays deleted across a subsequent re-ingestion.

### 6.5 One driver, one door
All Cypher goes through `db/neo4j_client.py`. Sinks and API modules call it; nothing else imports the driver.

---

## 7. Parsing-rule configuration

- Configs are **YAML files on disk**, the source of truth. The API reads/writes them; the UI never invents a second format.
- `pipeline/rules/schema.py` defines a **JSON Schema** for the config format, derived from NAA's real rule-file shape (`NAA/parsing-rules/br_requirements.yml`, lifted per [ADR-0001](adr/0001-lift-deterministic-extraction-not-llm.md)) — not chunking parameters or LLM prompts. See `id`/`rule_id` in [CONTEXT.md](../CONTEXT.md#rule-id).
- The Rules page renders a form **generated from the JSON Schema**. Validation errors surface inline before save.
- **Live preview** is the centerpiece: user picks a fixture or uploads a sample doc, the backend runs the real pipeline with `DryRunSink`, and the UI shows extracted entities and relationships (a simple list/table is sufficient in v1; rendering the preview in NVL is a nice-to-have, not required). Rule editing without preview is the failure mode this feature exists to eliminate — "writing YAML blind and discovering it's wrong after a 20-minute run."

Config shape (resolved against NAA's real format — see [CONTEXT.md](../CONTEXT.md#rule-file)):
- **Docx rule files:** `id_pattern`/`id_format` (item-id recognition), `title_from`, `category_signals` (regex, all matches collected), `named_extractions` (regex with `group`/`transform`/`filter`/`deduplicate`/`sort`), `context` collection flags. Each `category_signal`/`named_extraction` carries a stable `id` in addition to its editable `name` (see below).
- **Obsidian source config:** folder→entity-type mapping, fallback keyword signals, relationship-inference keywords, and which vault folders to scan — moved out of NAA's hardcoded Python into this same per-config YAML (see [ADR-0004](adr/0004-classification-rules-in-yaml-config.md)).
- No chunking parameters, no LLM prompts — extraction is deterministic pattern matching (ADR-0001).

**Rule IDs must be stable across edits** — they are the join key for correction analytics (§6.4). Each `category_signal`/`named_extraction` gets a separate, generated `id` field independent of its editable `name`; the Rules page form never lets renaming change the `id`. See [ADR-0005](adr/0005-stable-rule-id-separate-from-name.md).

---

## 8. API surface (complete — do not grow without spec change)

```
POST   /jobs                      {source_type, source_path, sinks[], config_id} → {job_id}
GET    /jobs                      → job history (paginated)
GET    /jobs/{id}                 → status, progress %, per-doc results
                                    (skipped | updated | failed | orphan-flags)
POST   /jobs/{id}/cancel

GET    /configs                   → list rule sets
GET    /configs/{id}
POST   /configs                   (validated against JSON Schema)
PUT    /configs/{id}
POST   /configs/{id}/preview      {sample: fixture_id | uploaded file} → ExtractionResult (no writes)

GET    /graph/subgraph            ?center_id&depth&types[] → {nodes[], relationships[]} shaped for NVL
GET    /graph/search              ?q= → node hits (to find a center node for the canvas)
POST   /graph/relationships       {from_id, to_id, type} → MERGE on (from_id, to_id, type):
                                    creates with origin=curated, or promotes an existing
                                    same-type edge to curated if one already exists (ADR-0011) (logs §6.4)
PUT    /graph/relationships/{id}  {type} → retype; origin becomes curated              (logs §6.4)
DELETE /graph/relationships/{id}  → logs §6.4 first (tombstones the edge, ADR-0010), then deletes
```

Jobs run in-process via async task + a runner that supports progress reporting and cancellation. No external queue (no Celery/Redis) — single-user/small-team tool; SQLite job store is enough.

Relationship creation validates `type` against `kg-schema` and rejects unknown types. `GET /graph/subgraph` enforces a node cap (default 300) and returns a `truncated` flag when hit.

---

## 9. Frontend pages

### Ingest
Source type picker (Obsidian / docx folder) → path input → sink checkboxes (graph / vector / both) → config selector → Run. Live progress (poll `GET /jobs/{id}`; SSE is explicitly avoided — it has caused problems in our corporate proxy environment). Results table per doc: skipped / updated / failed / orphan warnings, with error detail expandable.

### Rules
Two panes. Left: schema-generated form for the selected config. Right: preview panel — sample selector, "Preview" button, extracted entities & relationships table with the `rule_id` that produced each. Save writes YAML.

### Graph
Search box → pick center node → NVL canvas renders `subgraph`. Interactions, in NVL terms:
- Click = select (NVL selection state); Ctrl/Cmd-click = multi-select.
- With exactly two nodes selected in order: "Create relationship" button → type dropdown (from kg-schema) → `POST /graph/relationships` → `addAndUpdateElementsInGraph` to sync canvas without refetch.
- Select an edge: side panel shows properties (`origin`, `rule_id`, `source_doc`) + Retype / Delete actions → `removeRelationshipsWithIds` on success.
- Visual encoding: edge style differs by `origin` (e.g. solid = explicit, dashed = extracted, colored = curated); `orphaned: true` edges rendered in warning color.
- Expand-node (fetch neighbors of a node and merge into canvas) is in scope; it is a second `subgraph` call centered on that node.

No node creation, no node property editing, no bulk operations in v1.

---

## 10. Build phases and acceptance criteria

Implement strictly in order. Each phase has a gate; do not start the next phase until the gate passes.

### Phase 1 — Pipeline core via CLI (no API, no UI)
Lift NAA extraction into the adapter architecture. `cli.py` runs: `python cli.py ingest --source obsidian --path ./fixtures/vault --sink neo4j --config default.yml`.

**Gate:**
- Fixture vault and fixture docx set ingest successfully into a local Neo4j.
- Obsidian wikilinks appear as `origin: explicit` edges; inferred edges as `origin: extracted` with a `rule_id`.
- Re-running with no changes reports all docs skipped and performs zero graph writes.
- Modifying one fixture doc and re-running updates only that doc's extracted elements.
- A manually inserted `origin: curated` edge survives the re-run (§6.2), and deleting its endpoint's source content flags it orphaned (§6.3).
- **Golden-fixture parity check:** run NAA's current parser and Loom's ported adapter against the same fixture vault/docx and assert the extracted nodes/edges match — proves the port preserved NAA's behavior. Replaces the original F1-benchmark language, which assumed LLM-based extraction; see [ADR-0007](adr/0007-golden-fixture-parity-gate.md).

### Phase 2 — Job runner + FastAPI (jobs, configs, preview)
**Gate:** a job started via `POST /jobs` is observable through completion via polling; cancel works mid-run; `preview` returns the identical extraction a real run would produce for the same doc+config (assert by comparison in a test); invalid config rejected with schema errors.

### Phase 3 — Rules page
**Gate:** a user can open a config, change a rule, preview against a fixture, see the diff in extracted output, and save — without touching a YAML file by hand. The saved YAML round-trips (load → form → save) byte-stably apart from formatting.

### Phase 4 — Graph page
**Gate:** search → render subgraph → create a typed relationship between two selected nodes → it persists in Neo4j with `origin: curated` → a subsequent re-ingestion of the underlying docs leaves it intact. Delete and retype work and are recorded in `corrections`; a deleted edge stays deleted across a subsequent re-ingestion of its source doc (tombstoning, ADR-0010), not silently recreated. Creating a relationship over an already-existing same-type edge promotes it to curated rather than duplicating it (ADR-0011). Edge styling reflects `origin`.

### Phase 5 — Vector sink
**Gate:** adding ChromaDB touches only `pipeline/sinks/vector.py` + registration + UI checkbox. If it requires edits to `core.py` or source adapters, stop and fix the abstraction instead. "Both" sinks in one job produces graph + vector output from a single extraction pass. The vector sink chunks each document's raw text independently of the entity-extraction rule engine and embeds those chunks (ADR-0012); chunking parameters live in sink-specific config, not the rule YAML (§7).

---

## 11. Testing expectations

- Unit tests for: hash-skip logic, curated-immunity, orphan detection, rule engine application, schema validation of configs.
- Integration test per phase gate, runnable against a disposable Neo4j (docker-compose file included in repo).
- Fixtures: a mini Obsidian vault (≥ 8 notes with wikilinks, at least one note that links to a non-existent note) and ≥ 3 docx files (one plain prose, one with tables, one that intentionally triggers zero extractions).

## 12. Explicitly deferred (recorded so they are not re-litigated)

xlsx/pptx source adapters · node CRUD in canvas · correction-rate analytics UI · visual rule builder · SharePoint · auth/multi-tenancy · MCP anything · full-reingest UI · NVL-rendered preview in Rules page.

## 13. Open items requiring human input before Phase 1

Items 1–4 were resolved by grilling against the actual NAA source (`D:\Cloned Projects\NAA`) — see §1, §5, §7, and `docs/adr/0001`–`0007`.

1. ~~Paths to NAA source~~ — resolved, see §1.
2. ~~Example of the current NAA YAML parsing config~~ — resolved: `NAA/parsing-rules/br_requirements.yml`, see §7.
3. ~~Location of the extraction validation dataset~~ — moot: no such dataset or LLM-benchmark harness exists in NAA; replaced by the golden-fixture parity gate (ADR-0007).
4. ~~Confirmation of Obsidian mappings~~ — resolved: note→type and wikilink→relationship mappings are real code (`NAA/pipeline/src/config.py`), now moving into per-config YAML (ADR-0004) rather than staying fixed.
5. Neo4j and ChromaDB connection details for local dev (put in `.env`, never in code) — still open; an operational/setup task, not a design decision.
