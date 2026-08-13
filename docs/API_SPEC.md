# BASTION — API Spec (v1)

Base URL: `/api/v1`

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
  "poll_url": "/api/v1/approvals/{id}"
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

**Unauthenticated until Phase 5** (docs/ARCHITECTURE.md §11, §13) — every
endpoint below takes an explicit `org_id` param/field instead of deriving it
from a session, since JWT auth + RBAC is Phase 5's job by BUILD_PLAN.md's own
order. Multi-tenancy isolation is still enforced now, just against this
explicit-`org_id` shape (Phase 5 only changes where `org_id` comes from).

### Auth
- `POST /auth/login` — email/password → access + refresh token
- `POST /auth/refresh` — refresh token → new access + refresh token pair (rotation, see AUTH.md)
- `POST /auth/logout` — revoke current refresh token family

**Not yet built (Phase 5).**

### Agents & Policies
- `GET /agents` / `POST /agents` / `GET /agents/{id}` — **not yet built (Phase 5)**; until
  then, agents are inserted directly via SQL (see `docs/PROGRESS.md`).
- `POST /policies` — body: `{ "org_id": "uuid", "name": "...", "definition": [...] }`.
  Creates a new version (never mutates); compiles the definition immediately
  (400 `INVALID_POLICY_CONDITION` on an unsafe/malformed condition expression,
  not a 500) even though it isn't active yet.
- `GET /policies?org_id=uuid` — all versions for an org.
- `POST /policies/{id}/activate` — deactivates every other version in the same
  policy set, activates this one, hot-reloads every interceptor instance via
  Redis pub/sub (no restart).

### Approvals
- `GET /approvals?org_id=uuid` — pending approvals for an org (BUILD_PLAN.md's
  `?status=pending` is implicit: this endpoint only ever returns pending ones).
- `POST /approvals/{id}/approve`
- `POST /approvals/{id}/deny`

Both approve/deny return the updated `ApprovalRequestResponse` shape (above);
`resolved_by` is always `null` until Phase 5. A 409 `APPROVAL_NOT_PENDING` if
the approval was already resolved (or never existed) — resolution only ever
transitions a genuinely `pending` row, so two approvers racing each other
isn't a silent overwrite.

A plain HTML/JS approver page (no build step, no 3D view — BUILD_PLAN.md
Phase 3 explicitly calls this out as the acceptable interim UI) is served at
`GET /approvals-ui`.

### Traces
- `GET /traces?agent_id=&status=&from=&to=` — list/search
- `GET /traces/{trace_id}` — full replay data (folded event stream + graph)
- `GET /traces/{trace_id}/events` — raw event list (for the inspector panel)

## Realtime API

### `WS /live/{agent_id}`
Frontend subscribes here for live graph deltas while an agent is actively running.
```json
// Server → client messages
{ "type": "node_added", "node": { "span_id": "...", "tool_name": "...", "status": "pending" } }
{ "type": "node_updated", "span_id": "...", "status": "allowed", "latency_ms": 120, "cost": 0.002 }
{ "type": "edge_added", "from": "parent_span_id", "to": "span_id" }
```

## Error format (consistent across all endpoints)
```json
{ "error": { "code": "POLICY_NOT_FOUND", "message": "...", "request_id": "uuid" } }
```
Every response includes a `request_id` for correlating with logs — non-negotiable for a production-grade API.
