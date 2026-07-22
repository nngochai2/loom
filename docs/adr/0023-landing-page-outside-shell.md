# 0023 — Landing page: pre-shell entry point, not a fourth nav tab

## Status
Accepted

## Context
A landing/home page was proposed: navbar + hero + three cards (Obsidian→Graph, Documents→Graph, Documents→Vector) as an entry point ahead of the Ingest page. This appears to collide with ADR-0013 (persistent top nav with exactly three tabs: Ingest/Rules/Graph) and with spec §9's Ingest page, which is a single form (source picker × sink checkboxes), not three separate destinations. Resolved via `/grilling`.

## Decision
- The landing page sits **outside** the ADR-0013 shell, at `/`, with **no persistent top nav**. It is a one-time entry ramp for an already-oriented session, not a peer page to Ingest/Rules/Graph — the three-tab nav only appears once the user has landed on `/ingest`.
- **No scroll gate.** Hero copy and the cards are both visible in one normal-height page; the desktop-only, wide-viewport assumption (ADR-0013) means there's no need to gate content behind a scroll-to-reveal, which is a marketing-site pattern this internal tool doesn't need.
- **Navbar is branding only** (Loom name/logo), no nav links — it isn't a peer of the app shell's nav and shouldn't look like one.
- **Three cards are shortcuts, not an exhaustive menu.** They cover the three common source×sink combinations; a fourth plain "configure manually" link drops into the blank Ingest form for the combinations they don't cover (Obsidian→Vector, Obsidian→Both, Documents→Both).
- **Single click, whole card is the hit target.** Clicking a card navigates straight to `/ingest` with source and sink pre-filled — no intermediate confirm step, since nothing destructive happens before the user hits Run on the Ingest page itself.

## Consequences
- `frontend/src/pages/Landing.tsx` (or equivalent) is a standalone route, not rendered inside the shell component that owns the Ingest/Rules/Graph nav (ADR-0013's `App.tsx` shell).
- The Ingest page (issue #10) must accept pre-fill values (source type, sink selection) via route state or query params, since cards navigate into it with choices already made.
- If a future requirement makes the landing page something users return to mid-session (not just a one-time entry ramp), revisit the "outside the shell" decision rather than bolting a nav link on ad hoc.
