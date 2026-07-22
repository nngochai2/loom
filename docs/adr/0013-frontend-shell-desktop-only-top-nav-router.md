# 0013 — Frontend shell: desktop-only, top nav + react-router

## Status
Accepted

## Context
Spec §9 fixes what each of the three pages (Ingest/Rules/Graph) contains but not how a user moves between them or what viewport the layout targets. Issue #10 is the frontend scaffold ticket — whatever it picks becomes load-bearing for #11/#13/#14, built as separate tickets (possibly separate agent sessions) on top of it. Resolved via `/grill-with-docs` before starting #10.

Spec §2 already rules out multi-tenancy and treats this as a single-user/small-team tool run from a workstation, which removes any mobile/tablet use case.

## Decision
- **Desktop-only.** No responsive breakpoints, no collapsed/hamburger nav. Design for a wide viewport; Graph's NVL canvas and Rules' two-pane layout both want the horizontal space.
- **Persistent top nav bar** with three tabs (Ingest / Rules / Graph), not a sidebar — a sidebar would permanently spend horizontal width the canvas-heavy pages want.
- **react-router**, with real URLs (`/ingest`, `/rules`, `/graph`) rather than in-memory tab state, so refresh/back-button/bookmarking behave normally.

## Consequences
- `frontend/src/App.tsx` (or equivalent) owns the router and the persistent top-nav shell; each page component owns its full body below the nav.
- No mobile testing/QA pass is required or expected for any frontend ticket.
- If a future requirement introduces a genuine mobile use case, this ADR should be revisited rather than pages growing ad hoc responsive CSS.
