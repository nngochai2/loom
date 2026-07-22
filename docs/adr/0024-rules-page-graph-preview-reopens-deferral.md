# 0024 — Rules page gains a graph preview, narrowing spec §12's deferral

## Status
Accepted

## Context
Spec §12 explicitly deferred "NVL-rendered preview in Rules page" (and, separately, "visual rule builder") out of v1 scope, and spec §9 / issue #11 fixed the Rules page's preview panel as a single table (sample selector → Preview button → extracted entities/relationships table with `rule_id`). A graph visualization of what a rule config produces was proposed as an addition to that panel. Resolved via `/grilling` to decide whether to reopen the deferral and, if so, its exact shape.

## Decision
- **Reopen "NVL-rendered preview in Rules page."** It is removed from spec §12's deferred list. **"Visual rule builder" remains deferred** — this ADR only reopens *read-only graph rendering of preview output*, not visually authoring/editing rules by manipulating a graph. The left-pane schema-generated form remains the only way to edit a rule.
- **Supplements, does not replace, the existing table.** The table is what makes `rule_id` traceability legible (which rule produced which item); the graph is better at showing structure/shape. Both stay.
- **Same trigger as today: the "Preview" button.** Not per-keystroke live update. One `POST /configs/{id}/preview` call now populates both the table and the graph, rather than doing debounced re-extraction on every edit.
- **Isolated to the preview call's output.** The graph shows only what this rule config extracts from the sample doc — no merge with existing Neo4j graph state. Resolving extracted entities against real graph nodes is out of scope (adjacent to the also-deferred correction-rate analytics work).
- **Source-agnostic.** Applies to both Obsidian and docx rule configs — the Rules page is one component for both config types, not specialized here.
- **Reuses NVL and ADR-0017's edge-origin encoding.** Everything in a preview is by definition `origin: extracted` (nothing's persisted/curated yet), so edges render dashed gray uniformly. No separate graph-rendering library.
- **Tabs, not a simultaneous split.** The right pane's real estate is already half the viewport (two-pane layout, ADR-0013 desktop-only). Table and graph are tabs within that pane, each getting full pane space when active, rather than both cramped side by side.
- **Click-to-inspect interactivity**, mirroring the Graph page's existing pattern (spec §9: "Select an edge: side panel shows properties"): clicking a node or edge in the preview graph shows its properties (entity type, or `rule_id` for edges) in a side panel. No cross-tab highlighting into the table — that's real engineering cost for a workflow the tabs already support in two clicks.

## Consequences
- Spec §9's Rules page description and §10's Phase 3 gate ("preview against a fixture, see the diff in extracted output") must be amended to cover the graph view, not just the table — done in the same change as this ADR.
- Issue #11 (Rules page) needs its scope corrected — it currently states "NVL rendering here is explicitly deferred," which this ADR reverses.
- The preview endpoint's response shape must carry enough structure (node/edge list matching NVL's expected shape) for both the table and graph to render from one call — a preview-endpoint contract concern, not just a frontend one.
- If "visual rule builder" (editing rules by manipulating the graph directly) is ever proposed, it should get its own ADR rather than being read as already-covered by this one.
