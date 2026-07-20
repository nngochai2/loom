# Loom

Loom is the write path of a knowledge-graph pipeline: it parses documents (Obsidian vaults, `.docx` specs) and ingests them into Neo4j through one pluggable pipeline abstraction. It's a from-scratch rebuild of the write-path half of an existing internal tool ("NAA"), which had bundled batch parsing together with unrelated MCP-server hosting concerns. The two halves now only share a contract: the graph database itself, versioned via `kg-schema/`. See [`docs/loom-spec.md`](docs/loom-spec.md) for the full spec and [`docs/adr/`](docs/adr/) for the decisions made while implementing it.

```
[SourceAdapter] → [Extraction] → [RuleEngine] → [SinkAdapter(s)]
```

Everything is one pipeline. Obsidian vs. docx are source adapters; Neo4j (and later ChromaDB) are sink adapters.

## Status

Phase 1 (pipeline core via CLI, no API, no UI) is implemented and gated:

| Piece | Status |
|---|---|
| Schema contract (`kg-schema`) + SQLite operational store | done |
| Obsidian → Neo4j ingest (CLI) | done |
| Docx → Neo4j ingest (CLI), rule engine + rule-file schema | done |
| Incremental re-ingestion, curated immunity, orphan flagging | done |
| Golden-fixture parity gate (proves the port preserves NAA's real extraction behavior) | done |
| FastAPI + job runner, Rules page, Graph correction canvas, vector sink | not started (Phase 2+) |

155 backend tests passing, `mypy --strict` clean.

## Extraction

Extraction is deterministic, regex/rule-based pattern matching — lifted from NAA's actual parsing code, not an LLM (see [ADR-0001](docs/adr/0001-lift-deterministic-extraction-not-llm.md)):

- **Obsidian** (`app/pipeline/sources/obsidian.py`) — parses vault `.md` files: wikilinks become `origin: explicit` relationships (ground truth from the document itself, bypassing rule inference entirely), folder/keyword signals classify notes into entity types. Classification config lives in per-vault YAML, not hardcoded Python ([ADR-0004](docs/adr/0004-classification-rules-in-yaml-config.md)).
- **Docx** (`app/pipeline/sources/docx.py` + `app/pipeline/rules/`) — a YAML rule file (`app/pipeline/rules/schema.py` defines its JSON Schema) drives a generic table-row rule engine (`app/pipeline/rules/engine.py`): an `id_pattern` recognizes item rows, `category_signals` and `named_extractions` apply regex against each row's text. Only the generic single-parent-link structuring is lifted from NAA's docx parser — its project-specific document hierarchy is deliberately not part of Loom's core ([ADR-0006](docs/adr/0006-generic-docx-parent-link-only.md)).

Every extracted/explicit graph element carries mandatory properties defined in [`kg-schema/schema.json`](kg-schema/schema.json): `origin`, `source_doc`, `content_hash`, `rule_id`, `schema_version`, `created_at`/`updated_at`. Rule files map their output onto a type already in `kg-schema`'s fixed, versioned entity/relationship enum — they never invent a Neo4j label directly ([ADR-0002](docs/adr/0002-fixed-versioned-schema-not-dynamic-labels.md)).

## Incremental re-ingestion

Re-running ingest against the same source is cheap and safe (spec §6):

- Unchanged docs (by content hash, tracked in SQLite) are skipped — zero graph writes.
- Changed docs get their prior non-curated contribution deleted, then rewritten.
- Docs removed from the source entirely get the same cleanup, plus their SQLite hash row dropped.
- `origin: curated` elements — human corrections made via the (future) graph canvas — always survive re-ingestion, even when re-extraction would otherwise recreate a duplicate.
- If re-ingestion would leave a curated edge pointing at a node that no longer exists, that edge is never auto-deleted — it's flagged `orphaned: true` and surfaced in the job result for a human to resolve.

## Getting started

Requires Python 3.11+, [`uv`](https://github.com/astral-sh/uv), and Docker (for Neo4j).

```bash
# Start Neo4j
docker compose up -d

# Install the backend (from backend/)
cd backend
uv pip install -e ".[dev]"
cp .env.example .env   # NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD

# Run an ingest
python cli.py ingest --source obsidian --path ./path/to/vault \
    --sink neo4j --config ./path/to/vault-config.yml --db ./loom.sqlite3

python cli.py ingest --source docx --path ./path/to/docs \
    --sink neo4j --config ./path/to/rules.yml --db ./loom.sqlite3
```

Omit `--db` for a one-shot full ingest with no hash-skip/doc-removal bookkeeping (the same shape the future `preview` endpoint needs via a `DryRunSink`).

## Development

```bash
cd backend
mypy app cli.py    # strict, must stay clean
pytest              # full suite; no live Neo4j required — sinks are tested against fakes/doubles
```

`backend/tests/fixtures/` holds a small fixture Obsidian vault and a docx fixture set (plain prose, a requirements table, an intentional zero-extraction case), exercised end-to-end through the real pipeline in `test_fixture_vault_integration.py` / `test_fixture_docs_integration.py`.

`backend/tests/test_golden_fixture_parity.py` is the Phase 1 exit gate ([ADR-0007](docs/adr/0007-golden-fixture-parity-gate.md)): it diffs Loom's ported adapters against a recorded snapshot of NAA's real parser output for the same fixtures, so a future change to an adapter can't silently drift from the behavior being preserved. The snapshot is regenerated (locally only, against the sibling NAA checkout — not a CI dependency) via `backend/scripts/generate_golden_fixture_snapshot.py`.

## Repository layout

```
loom/
├── backend/
│   ├── app/
│   │   ├── pipeline/
│   │   │   ├── core.py             # Pipeline.run — the orchestrator
│   │   │   ├── types.py            # SourceDoc, LoadedDoc, ExtractionResult, JobResult, ...
│   │   │   ├── sources/{base,obsidian,docx}.py
│   │   │   ├── rules/{engine,schema}.py
│   │   │   └── sinks/{base,neo4j}.py
│   │   ├── jobs/store.py           # SQLite: doc-hash tracking, correction log
│   │   └── db/neo4j_client.py      # the only module importing the bolt driver
│   ├── cli.py                      # Phase 1 entry point: run the pipeline without API/UI
│   ├── scripts/generate_golden_fixture_snapshot.py
│   └── tests/
│       └── fixtures/               # fixture vault, docx set, golden snapshots
├── docs/
│   ├── loom-spec.md
│   ├── adr/                        # one file per architectural decision
│   └── agents/                     # issue-tracker/triage/domain-doc conventions for agent work
├── kg-schema/                      # versioned entity/relationship schema — the contract with NAA's read path
└── docker-compose.yml              # Neo4j only, for local dev
```

Issues and PRDs are tracked as GitHub issues on this repo — see [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md).
