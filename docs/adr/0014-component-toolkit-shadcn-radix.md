# 0014 — Component toolkit: shadcn/ui + Radix primitives on Tailwind

## Status
Accepted

## Context
Tailwind is fixed by spec §3, but the spec doesn't say how buttons, modals, dropdowns, confirm dialogs, and tables get built on top of it. Four frontend tickets (#10/#11/#13/#14) will each need these, and building each from scratch independently risks visible drift (different modal behavior, different focus handling) across pages that are supposed to feel like one app. The Graph page (#13) in particular needs an accessible modal/panel for its edge-selection Retype/Delete actions, and Rules (#11) needs confirm-before-destructive patterns too. Resolved via `/grill-with-docs`.

## Decision
Use shadcn/ui components (copied into the repo, not an opaque npm dependency) built on Radix UI's unstyled accessible primitives, styled with Tailwind. This covers buttons, dialogs/modals, dropdowns, and toasts. `react-jsonschema-form` (already fixed by spec §7) remains the form generator for the Rules page specifically — shadcn provides the surrounding chrome (inputs styling, buttons), not the schema-driven form logic itself.

Rejected alternatives:
- Hand-rolled Tailwind components: would mean re-solving focus-trapping/keyboard nav for every modal, and higher risk of styling drift across independently-built tickets.
- A full pre-styled kit (Mantine/Chakra/etc.): brings its own theming system that fights Tailwind's utility-first approach, and is heavier than this tool's scope calls for.

## Consequences
- shadcn components live under `frontend/src/components/ui/` (its standard convention) and are copied in via its CLI as needed — not listed as a single opaque `package.json` dependency the way a full kit would be.
- All 4 frontend tickets should reuse the same `components/ui/` primitives rather than each building their own button/modal/dropdown variants.
- Toast notifications and confirm dialogs (see ADR-0016) are built on this same base.
