# 0003 — Entity/relationship vocabulary is seeded from NAA's real classifier, not the spec's placeholder

## Status
Accepted

## Context
The original loom-spec (§5) proposed entity types `MODULE, SERVICE, API, DATABASE_TABLE, BUSINESS_TERM, CODING_CONVENTION, PATTERN` and relationship types `DEPENDS_ON, IMPLEMENTS, USES, CONNECTS_TO, FOLLOWS, VIOLATES`. Neither list matches what NAA's real, lifted classifier (ADR-0001) actually produces:

- **Real Obsidian note types** (`NAA/pipeline/src/config.py` — `SUBFOLDER_TYPE_MAP`/`TYPE_SIGNALS`): `ARCHITECTURE`, `CONVENTION`, `TASK`, `BUSINESS_TERM`, `NOTE`, `TAG`.
- **Real wikilink relationship inference** (`REL_KEYWORDS`): `DEPENDS_ON`, `EXTENDS`, `USES`, `CONNECTS_TO`, `IMPLEMENTS`, `RELATES_TO`, `FIXES`, `RESOLVES`, `CAUSED_BY`, `FOLLOWS`, `VIOLATES`, plus `LINKS_TO` (default) and `TAGGED_WITH` (tag-section links).
- **Real docx entity type**: just `BR` — specific to the user's current eInvoice project, not a generic Loom concept.

## Decision
- Entity types: seed the enum with the real Obsidian vocabulary (`ARCHITECTURE`, `CONVENTION`, `TASK`, `BUSINESS_TERM`, `NOTE`, `TAG`). Do **not** carry over `BR`, and do not adopt the spec's original placeholder list.
- Docx entity types: ship with **zero** defaults. The first docx rule file (BR-shaped or otherwise) requires its author to add its entity type to `schema.json` via a schema-version bump (per ADR-0002) — there is no generic `REQUIREMENT` fallback type.
- Relationship types: lift the full real set — all 12 wikilink-context-inferred types plus `LINKS_TO` and `TAGGED_WITH` — rather than the spec's trimmed 6-type list.

## Consequences
- The vocabulary is real from day one for Obsidian, but starts empty for docx — the second rule file authored (for any project) forces a conscious naming decision instead of inheriting `BR`'s project-specific meaning.
- `kg-schema/__init__.py`'s generated Python constants will need regenerating whenever `schema.json` changes; this is expected, not exceptional, workflow.
