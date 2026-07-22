# 0015 — TanStack Query for all server state, including job polling

## Status
Accepted

## Context
Spec §9 requires the Ingest page to poll `GET /jobs/{id}` for live progress and explicitly rules out SSE ("has caused problems in our corporate proxy environment"). Polling via hand-rolled `setInterval` + `useEffect` is a common source of bugs (stale closures over job id/status, forgotten cleanup on unmount, races between a manual refetch and the interval). The Rules page (#11) and Graph page (#13) also need GET/mutate patterns against the Configs and Graph APIs. Resolved via `/grill-with-docs` before #10, since the scaffold ticket sets up whatever pattern the rest inherit.

## Decision
Use TanStack Query (`@tanstack/react-query`) for all server-state reads and writes across all pages, including job-status polling via `useQuery`'s `refetchInterval` option (not a custom `setInterval`). Mutations (`POST /jobs`, `PUT /configs/{id}`, `POST /graph/relationships`, etc.) go through `useMutation`.

## Consequences
- `frontend/src/api/client.ts` (per spec §4.2's repo layout) provides the fetch functions; TanStack Query wraps them in `useQuery`/`useMutation` hooks per page.
- One shared `QueryClientProvider` at the app root (`frontend/src/App.tsx`).
- Loading/error states across all pages follow TanStack Query's `isLoading`/`isError` conventions rather than each page inventing its own state shape.
- Adds one dependency (`@tanstack/react-query`) beyond the spec's fixed frontend stack (§3) — justified by removing a whole class of polling bugs and keeping the 4 independently-built tickets consistent.
