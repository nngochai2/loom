# 0002 — kg-schema is a fixed, versioned enum; rule files map into it

## Status
Accepted

## Context
NAA's live docx path lets each rule YAML declare its own Neo4j label directly (`node_label: BR`) — there is no central type enum for docx-sourced nodes. Obsidian notes, by contrast, use one label (`Note`) with a `type` property drawn from a fixed set. The loom-spec (§1, §5) wants "the database is the contract" between Loom (write path) and the future NAA MCP server (read path), calling `kg-schema` "the only coupling point... treated as a versioned artifact."

Dynamic per-rule labels are incompatible with that promise: the read side would have no way to know the graph's vocabulary except by introspecting whatever labels happen to exist.

## Decision
`kg-schema/schema.json` defines a fixed, versioned enum of entity and relationship types. Rule files (docx or otherwise) must map their output onto a type already in the enum — they do not declare new Neo4j labels freely. Adding a new type is a schema-version bump (as already stated in spec §5), a deliberate, tracked change rather than something that happens silently when someone edits a rule file.

## Consequences
- Authoring a new docx rule file for a type not yet in the schema requires updating `kg-schema/schema.json` and bumping `VERSION` first — see ADR-0004 for how the config UX handles this.
- The future NAA MCP read-path can always enumerate the graph's vocabulary from `schema.json` without querying the graph.
- See ADR-0003 for what the enum actually contains on day one.
