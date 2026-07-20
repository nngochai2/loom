# 0011 — Creating a relationship over an existing same-type edge promotes it, rather than duplicating

## Status
Accepted

## Context
§8's `POST /graph/relationships {from_id, to_id, type}` didn't specify what happens when an edge of that same type already exists between the two selected nodes (extracted or explicit). NAA's lifted upsert code (`graph.py`) uses Neo4j `MERGE` on `(src)-[r:TYPE]->(tgt)` throughout — it never creates parallel duplicate edges of the same type between the same two nodes.

## Decision
`POST /graph/relationships` MERGEs on `(from_id, to_id, type)`, matching NAA's existing pattern: if an edge of that type already exists, its `origin` flips to `curated` (making it immune per §6.2) instead of a second parallel edge being created.

## Consequences
- The endpoint's response should indicate whether it created a new edge or promoted an existing one, so the UI (§9 Graph page) can react correctly (e.g. `addAndUpdateElementsInGraph` update vs. insert).
- No behavioral change needed elsewhere — this only clarifies an underspecified case using the same MERGE convention already used everywhere else in the lifted graph-write code.
