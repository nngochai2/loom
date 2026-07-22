# 0016 — Toast notifications + modal confirm dialogs for feedback and destructive actions

## Status
Accepted

## Context
Several actions across the frontend need success/error feedback (job failed, config save succeeded, preview error, invalid schema) and several are destructive (cancel a running job, delete a graph relationship). Left undecided, different tickets would likely invent different patterns (inline banners in one page, silent failures in another). Resolved via `/grill-with-docs`.

## Decision
- **Toast notifications** (via a shadcn/Radix-based toast primitive, see ADR-0014) for transient success/error feedback, from one toast provider mounted at the app root.
- **Modal confirm dialogs** before any destructive action — cancelling a job, deleting a relationship (§8 `DELETE /graph/relationships/{id}`) — using the same shadcn dialog primitive.

## Consequences
- One toast provider and one reusable confirm-dialog component/hook, shared by all pages rather than rebuilt per ticket.
- Destructive-action confirms are a hard requirement for #13's Delete action and any future "cancel job" control on #10's Ingest page.
