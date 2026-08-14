# BASTION — API Spec (v1)

**Base URL, corrected (Phase 11 drift check)**: no `/api/v1` prefix exists anywhere in the actual
interceptor/aggregator code — every endpoint below is served at the bare path shown (`/intercept`,
`/approvals/{id}`, `/policies`, etc.), confirmed against both services' real routes and the generated
OpenAPI schemas in `docs/api/`. This doc originally specced a versioned base URL that was simply never
implemented; the frontend's dev proxy (`frontend/vite.config.ts`) and every example in this document
already assumed the bare-path reality, so this correction changes documentation, not behavior. Path
versioning, if ever needed, is still open — not a decision made and reversed, just never made.

## Machine API (called by the SDK, high frequency, latency-critical)

### `POST /intercept`
Called by the SDK for every tool call.
```json
// Request
{
  "trace_id": "uuid (client-generated if root call)",
  "parent_span_id": "uuid | null",
  "tool_name": "payments.charge",
  "args": { "amount": 75, "currency": "USD" },
  "agent_id": "uuid"
}
// Response (allowed)
{
  "span_id": "uuid",
  "decision": "allowed",
  "policy_id": "uuid",
  "result": { /* passthrough result of the real call, if BASTION proxied it */ }
}
// Response (blocked)
{
  "span_id": "uuid",
  "decision": "blocked",
  "policy_id": "uuid",
  "reason": "amount exceeds $50 auto-approve threshold and no approval configured"
}
// Response (pending approval — returns immediately, does NOT block;
// see "long-poll lives on GET /approvals/{id}" below)
{
  "span_id": "uuid",
  "decision": "pending_approval",
  "approval_request_id": "uuid",
  "poll_url": "/approvals/{id}"
}
```
**`pending_approval` never blocks this call** — see docs/ARCHITECTURE.md §13
for why holding `/intercept` open for a human-timescale decision would break
the stateless, horizontally-scalable hot-path story. The actual long-poll is
`GET /approvals/{id}` below; the SDK calls it in a loop.
Auth: `Authorization: Bearer <agent api key>`. The bearer key identifies the
agent; the request body's `agent_id` must match the authenticated agent or
the call is rejected (`AGENT_MISMATCH`, 403) — the key alone determines
identity, `agent_id` in the body is a stated-identity check, not a trust
source.

### `POST /spans/{span_id}/complete`
**Not in the original spec — added in Phase 1, see docs/ARCHITECTURE.md §7.**

ARCHITECTURE.md §2.2 describes the interceptor itself "executing the real
downstream call" when a call is allowed. But neither this doc's
`InterceptRequest` nor DATA_MODEL.md gives the interceptor anything to reach
a downstream system with (no target URL, no DSN, no adapter registry) — so
in practice, **the SDK executes the real call locally** after `/intercept`
returns `allowed`, then reports the outcome here. This is also why the
<50ms p99 target (ARCHITECTURE.md §6) is coherent: it measures interceptor
decision latency only, cleanly separated from the real call's own latency,
which this endpoint reports back.

Only valid for a `span_id` that `/intercept` most recently returned
`"allowed"` for; emits `CallCompleted` or `CallFailed`.

```json
// Request
{
  "status": "completed",           // or "failed"
  "latency_ms": 42.5,
  "cost": 0.002,                    // optional
  "result": { "...": "..." },       // optional, opaque
  "error": null                     // set when status is "failed"
}
// Response
{ "span_id": "uuid", "status": "completed" }
```
Auth: `Authorization: Bearer <agent api key>`, same agent-match rule as `/intercept`.

### `GET /approvals/{id}`
The SDK's actual long-poll target (not `/intercept` — see above and
docs/ARCHITECTURE.md §13). Blocks up to `APPROVAL_LONG_POLL_SECONDS`
(default 25s) waiting for a resolution; returns the current status either
way (still `"pending"` if the window elapsed with no decision — the SDK
calls again). Woken early by a Redis pub/sub signal from approve/deny/expiry,
not busy-polling. Past the approval's absolute deadline
(`APPROVAL_TTL_SECONDS`, default 300s), this call itself flips it to
`"timed_out"` (checked lazily here, not by a background sweeper) and emits
`ApprovalDenied`.
Auth: `Authorization: Bearer <agent api key>` — the span's original agent, same
`AGENT_MISMATCH` rule as `/intercept`.
```json
// Response
{
  "id": "uuid", "trace_id": "uuid", "span_id": "uuid",
  "status": "pending",              // | "approved" | "denied" | "timed_out"
  "requested_at": "2026-01-01T00:00:00Z",
  "resolved_by": null,               // always null until Phase 5 (no users table yet)
  "resolved_at": null
}
```

## Human/dashboard API

Auth per AUTH.md §2, implemented Phase 5: argon2id passwords, Ed25519-signed JWT access
tokens (15 min TTL), refresh token rotation with family-based reuse detection, RBAC.
Every endpoint below requires `Authorization: Bearer <access token>` and derives org
scoping from the token's claims — no `org_id` param/field anywhere (Phase 2-4 had one as
an explicit stopgap before auth existed; docs/ARCHITECTURE.md §11, §13 record that history).
A resource belonging to a different org 404s, never a distinguishable 403 — "doesn't exist"
and "exists but isn't yours" look identical to the caller.

### Auth
- `POST /auth/login` — `{ "email": "...", "password": "..." }` → `{ "access_token",
  "refresh_token", "token_type": "bearer", "role" }`. 401 `INVALID_CREDENTIALS` on failure
  (no distinction between "no such user" and "wrong password").
- `POST /auth/refresh` — `{ "refresh_token": "..." }` → a **new** token pair; the presented
  refresh token is immediately revoked (one-time use). Presenting an already-used token
  triggers reuse detection: the **entire token family** is revoked (401
  `REFRESH_TOKEN_REUSED`), forcing full re-login — this is what makes rotation meaningful
  against a stolen token (AUTH.md §2).
- `POST /auth/logout` — `{ "refresh_token": "..." }` → revokes the whole family.

No signup endpoint — AUTH.md doesn't spec one, and none of the phases call for it. Users are
inserted directly via SQL in dev/tests (`docs/PROGRESS.md`); `POST /users` would be natural
Phase-5-adjacent scope if a real registration flow is ever needed.

### Agents & Policies
- `GET /agents` / `POST /agents` / `GET /agents/{id}` — **not yet built**; agents are
  inserted directly via SQL (see `docs/PROGRESS.md`). Same non-spec gap as signup above.
- `POST /policies` (role: `owner`/`admin`) — body: `{ "name": "...", "definition": [...] }`.
  Creates a new version (never mutates); compiles the definition immediately (400
  `INVALID_POLICY_CONDITION` on an unsafe/malformed condition expression, not a 500) even
  though it isn't active yet.
- `GET /policies` (any role) — all versions for the caller's org.
- `POST /policies/{id}/activate` (role: `owner`/`admin`) — deactivates every other version
  in the same policy set, activates this one, hot-reloads every interceptor instance via
  Redis pub/sub (no restart). Org ownership is checked *before* any row is mutated, not
  filtered from the response afterward.

### Approvals
- `GET /approvals` (any role) — pending approvals for the caller's org (BUILD_PLAN.md's
  `?status=pending` is implicit: this endpoint only ever returns pending ones).
- `POST /approvals/{id}/approve` (role: `owner`/`admin`/`approver`)
- `POST /approvals/{id}/deny` (role: `owner`/`admin`/`approver`)

Both approve/deny return the updated `ApprovalRequestResponse` shape (above), with
`resolved_by` now populated from the authenticated user. A 409 `APPROVAL_NOT_PENDING` if the
approval was already resolved, belongs to a different org, or never existed — resolution
only ever transitions a genuinely pending row in the caller's own org, atomically, so two
approvers racing each other isn't a silent overwrite.

A plain HTML/JS approver page (no build step, no 3D view — BUILD_PLAN.md Phase 3 explicitly
calls this out as the acceptable interim UI) is served at `GET /approvals-ui`; it takes a
pasted access token, no login form (that's Phase 7's job).

### Traces
**Served by the aggregator, not the interceptor** (`aggregator/src/bastion_aggregator/main.py`)
— it owns the read-model these come from, and independently verifies the same JWTs (public
key only, AUTH.md's "without calling the auth service"). `agent_id`/`status`/`from`/`to`
filters aren't implemented yet.

- `GET /traces` (any role) — persisted (terminal-state) traces for the caller's org, newest
  first. An in-progress trace has no `trace_summaries` row by design (see `GET /traces/{id}`
  below) — it won't appear here until it finishes.
- `GET /traces/{trace_id}` — full replay: `trace_summaries.graph_snapshot` if a persisted
  projection exists (fast path), else folds `events` fresh — this is what makes an
  **in-progress** trace replayable too, not just completed ones (event-sourcing discipline:
  current state is always derivable from `events`, docs/ARCHITECTURE.md §14).
  ```json
  {
    "trace_id": "uuid", "agent_id": "uuid",
    "status": "running",              // | "completed" | "failed" | "had_blocks"
    "total_cost": 0.002, "total_calls": 3, "blocked_calls": 0,
    "started_at": "2026-01-01T00:00:00Z", "ended_at": null,
    "nodes": [
      { "span_id": "uuid", "parent_span_id": null, "tool_name": "tool.root",
        "status": "completed", "args": {}, "latency_ms": 42.1, "cost": null, "reason": null }
    ],
    "edges": [ { "from": "uuid", "to": "uuid" } ]
  }
  ```
- `GET /traces/{trace_id}/events` — raw event list, ordered by `sequence_number` (for the
  2D inspector panel, ARCHITECTURE.md §2.6).

## Realtime API

### `WS /live/{agent_id}`
Served by the aggregator. Frontend subscribes here for live graph deltas while an agent is
actively running — pushed straight from the same Postgres LISTEN/NOTIFY handler that
updates `active_traces` (ARCHITECTURE.md §14), no polling on either side.

Auth via **`?token=<access token>`** query param, not an `Authorization` header — a browser's
WebSocket API doesn't let JS set custom headers on the handshake. Connection is closed with
code `4401` (missing/invalid token) or `4403` (`agent_id` belongs to a different org than the
token's) — the 4xxx range is reserved for application-defined WebSocket close codes, mirroring
the HTTP endpoints' 401/403.

```json
// Server → client messages
{ "type": "node_added", "node": { "span_id": "...", "tool_name": "...", "status": "pending" } }
{ "type": "node_updated", "span_id": "...", "status": "allowed", "latency_ms": 120, "cost": 0.002, "reason": null }
{ "type": "node_updated", "span_id": "...", "status": "blocked", "reason": "blocked by policy rule for tool 'payments.transfer'" }
{ "type": "edge_added", "from": "parent_span_id", "to": "span_id" }
```
`reason` (added Phase 8, `docs/ARCHITECTURE.md` §17) carries the block/deny/failure reason for a
`blocked`/`failed` status, `null` otherwise — the same field the replay API's `GraphNode.reason`
already had; the live delta path was missing it until a real bug surfaced it (see §17's writeup).

`GET /metrics` (both interceptor and aggregator, Phase 9) — Prometheus text-exposition format,
`intercept_latency_seconds` and `policy_decisions_total{decision=}` on the interceptor. Deliberately
`include_in_schema=False`, so it's absent from the generated OpenAPI docs in `docs/api/` — noted here,
not silently missing.

## Error format (consistent across all endpoints)
```json
{ "error": { "code": "POLICY_NOT_FOUND", "message": "...", "request_id": "uuid" } }
```
Every response includes a `request_id` for correlating with logs — non-negotiable for a production-grade API.
