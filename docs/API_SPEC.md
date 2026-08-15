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
  "agent_id": "uuid",
  "idempotency_key": "uuid | null" /* v2, UPGRADE_ARCHITECTURE.md §3 — optional at the wire
                                       level, but the Python SDK always generates and reuses one
                                       per logical call across its own retries; omitting it gets
                                       v1's original behavior, always a fresh span, no dedup */
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

**Idempotency (v2, ADR-004/ADR-005)**: when `idempotency_key` is supplied, a repeated call with the
same `(agent_id, idempotency_key)` returns the original stored decision instead of re-evaluating —
same `span_id`, same response body, no duplicate `CallAttempted`/decision event. Under real
concurrency (two+ callers racing on the same key simultaneously), the loser(s) wait up to 2 seconds
for the winner's result; if it hasn't completed by then, `503 IDEMPOTENT_REQUEST_IN_PROGRESS`.

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
- `POST /auth/signup` (added post-Phase-9) — `{ "org_name": "...", "email": "...", "password": "..." }`
  → creates a **brand-new organization** plus its first user (role `owner`), then returns the same
  `TokenPairResponse` shape as login (auto-login after signup). 201 on success; 409
  `EMAIL_ALREADY_REGISTERED` if the email is already in use (`email` is globally unique, not per-org —
  migration `0005_users_auth.sql`); 422 on a password under 8 characters or a malformed email. This is
  always a *new* org — there's no invite/join flow, since joining an existing org via a bare email +
  password with no invite token would let anyone add themselves to any org they knew the name of.

Prior to this, users were inserted directly via SQL in dev/tests (`docs/PROGRESS.md`) — that path still
exists for tests and seed scripts, but real signup no longer requires it.

- `PATCH /auth/password` (any role, added post-launch) — body: `{ "current_password": "...",
  "new_password": "..." }`. 401 `INVALID_CURRENT_PASSWORD` if the current password doesn't match —
  required so a hijacked or left-open session can't silently change it without the real password ever
  being typed.

### Team (RBAC management)
- `GET /users` (any role) — every user in the caller's org, ordered by join date. Never includes a
  password or any credential.
- `POST /users` (role: `owner`/`admin`) — body: `{ "email": "...", "role": "owner"|"admin"|"approver"|"viewer" }`
  → `UserResponse` plus a one-time `temporary_password` field. This is **provisioning, not an email
  invite** — no email-sending infrastructure exists anywhere in this project, and the temp password is
  shared with the new teammate out of band by whoever created the account. 409
  `EMAIL_ALREADY_REGISTERED` on a duplicate email.
- `PATCH /users/{id}/role` (role: `owner`/`admin`) — body: `{ "role": "..." }`. 404 `USER_NOT_FOUND` for
  a nonexistent or cross-org target (same "doesn't exist" vs. "isn't yours" non-disclosure as everywhere
  else). 409 `LAST_OWNER` if the change would demote the organization's only remaining owner — that
  specific transition is blocked because it's a self-inflicted lockout (nobody left who can activate a
  policy, provision anyone, or promote someone back to owner), not blocked as "demotion" in general.

### API tokens (added post-launch — AUTH.md §4)
A third auth credential alongside agent keys and JWT sessions: a long-lived token a human hands to a
script/CI job to call this same management API. `authenticate_user` accepts a `bstn_pat_...` token
exactly like a JWT — identical RBAC, no separate weaker path.

- `GET /api-tokens` (any role) — the caller's **own** tokens only (personal, not org-shared like
  agents/policies). Never includes the raw token, only `token_prefix`.
- `POST /api-tokens` (any role) — body: `{ "name": "..." }` → `ApiTokenResponse` plus a one-time
  `token` field (`bstn_pat_` prefix; only the SHA-256 hash is ever stored, same as an agent key —
  no way to retrieve a lost token later, by design).
- `DELETE /api-tokens/{id}` (any role) — 204 on success. 404 `API_TOKEN_NOT_FOUND` if the token
  doesn't exist, is already revoked, **or belongs to a different user** (even in the same org) — same
  check-before-mutate, no-disclosure-on-cross-owner discipline as everywhere else in this API.

### Agents & Policies
- `POST /agents` (role: `owner`/`admin`) — body: `{ "name": "...", "policy_set_id": "uuid | null" }`
  → `AgentResponse` plus a one-time `api_key` field (raw key, `bastion_` prefix; only the SHA-256 hash
  is ever stored, same as every other agent API key — there is no way to retrieve a lost key later, by
  design). 404 `POLICY_SET_NOT_FOUND` if `policy_set_id` doesn't belong to the caller's org.
- `GET /agents` (any role) — all agents for the caller's org; never includes `api_key`, only the
  one-time create response does.
- `PATCH /agents/{id}` (role: `owner`/`admin`) — body: `{ "policy_set_id": "uuid | null" }`, reassigns
  which policy set an agent resolves to. Same org-ownership check-before-mutate discipline as
  `POST /policies/{id}/activate`; 404 `AGENT_NOT_FOUND` / `POLICY_SET_NOT_FOUND` as appropriate.

  Prior to this, agents were only ever inserted directly via SQL (`docs/PROGRESS.md`) — that path still
  exists for tests and seed scripts, but a real caller no longer needs it.
- `POST /policies` (role: `owner`/`admin`) — body: `{ "name": "...", "definition": [...],
  "based_on_version": 1 }`. Creates a new version (never mutates); compiles the definition
  immediately (400 `INVALID_POLICY_CONDITION` on an unsafe/malformed condition expression, not a
  500) even though it isn't active yet. `based_on_version` is optional (U4, v2 upgrade,
  `docs/adr/ADR-016`) — omit it to keep v1's original blind-append behavior with no conflict
  detection. Supplied, it must match the actual current latest version for that policy's name at
  write time, or the call fails with 409 `POLICY_VERSION_CONFLICT` instead of silently creating a
  version past one a concurrent editor already committed — the client should re-`GET /policies`,
  reconcile against the real current version, and retry with the fresh `based_on_version`.

  U6 (v2 upgrade, `docs/adr/ADR-015`): each rule in `definition` may also carry a `limits` object —
  `max_transaction_amount`, `calls_per_minute`, `org_spend_per_day`, `agent_llm_budget_per_hour`, all
  optional. Enforced only when that rule's `action` is `allow` and it matched; a violated limit turns
  the call into a `blocked` decision at `/intercept` time with a specific reason, same response shape
  as any other policy block. Independent of `limits`, `/intercept` also fails a call fast (same
  `blocked` shape, reason `"circuit breaker open for tool '<name>'"`) if that agent/tool pair's
  circuit breaker is currently OPEN — this isn't policy-configurable, it's a fixed system protection.
- `GET /policies` (any role) — all versions for the caller's org.
- `POST /policies/{id}/activate` (role: `owner`/`admin`) — deactivates every other version
  in the same policy set, activates this one, hot-reloads every interceptor instance via
  Redis pub/sub (no restart). Org ownership is checked *before* any row is mutated, not
  filtered from the response afterward.
- `POST /policies/simulate` (any role) — U15 (v2 upgrade), Policy Studio's simulator,
  `docs/adr/ADR-020`. Body: `{ "agent_id": "uuid", "tool_name": "...", "args": {...} }`.
  Resolves `agent_id` to that agent's real `default_policy_set_id`, then calls the exact same
  `policy_cache.get()` + `evaluate()` chain `/intercept` itself uses — never a separate
  simulated evaluator. Response: `{ "decision": "allow"|"block"|"require_approval", "reason":
  "...", "policy_id": "uuid", "policy_set_id": "uuid", "matched_rule_tool": "...",
  "configured_limits": {...} }`. `configured_limits` is informational only — this endpoint
  never calls `limits.check_and_apply_limits`/`circuit_breaker.is_open`, so a simulated call
  never consumes the agent's real rate-limit/spend budget or affects a real circuit breaker.
  404 `AGENT_NOT_FOUND` for a missing/cross-org `agent_id`.
- `GET /policies/{policy_set_id}/propagation` (any role) — U15 (v2 upgrade), Policy Studio's
  propagation-status panel, `docs/adr/ADR-020`. Compares Postgres's real active version for
  that policy set against this interceptor instance's real, live `policy_cache`. Response:
  `{ "policy_set_id": "uuid", "active_version": 3, "active_policy_id": "uuid",
  "this_instance_cached_version": 3, "propagated": true, "known_interceptor_instances": 1 }`.
  `known_interceptor_instances` is honestly always `1` — no multi-replica registry exists in
  this codebase (ADR-020); this reports the single instance actually handling the request, not
  a fabricated fleet count. 404 `POLICY_SET_NOT_FOUND` if there's no active policy for that set
  in the caller's org.

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
key only, AUTH.md's "without calling the auth service").

- `GET /traces` (any role) — persisted (terminal-state) traces for the caller's org, newest
  first. An in-progress trace has no `trace_summaries` row by design (see `GET /traces/{id}`
  below) — it won't appear here until it finishes. U16 (v2 upgrade), Trace Explorer: optional
  query filters, all combinable — `agent_id` (uuid), `status` (`completed`|`failed`|
  `had_blocks`), `tool` (matches any node's `tool_name` in the trace's folded graph), `policy`
  (matches a policy *name* — resolved via `events.payload->>'policy_id'`, since neither
  `trace_summaries` nor its `graph_snapshot` denormalizes a policy reference onto a node),
  `started_after`/`started_before` (ISO 8601 timestamps).
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

### Analytics (U16, v2 upgrade — FRONTEND_V2.md's supporting surfaces)
Also served by the aggregator. Every field traces back to a real aggregate over `events`/
`trace_summaries`/`policies`/`agents`/Redis circuit-breaker state — see `docs/adr/ADR-021` for
the exact definition (and why) of every place the spec's own illustrative text ("99.97%
availability") isn't literally something this system tracks.

- `GET /threats?window_days=30` (any role) — Threat Center. "Threats" means blocked calls (no
  prompt-injection-specific detector exists). Returns `blocked_calls_total`,
  `top_violated_policies` (`[{policy_id, policy_name, block_count}]`, top 10), and a daily
  `timeline` (`[{day, blocked_count}]`).
- `GET /agents/{agent_id}/health?window_days=30` (any role) — Agent Health. Real call-count
  breakdown (`calls_total`/`blocked_total`/`failed_total`/`pending_approval_total`), real
  `avg_latency_ms`/`estimated_cost_total`, `top_tools`, plus a composite `health_score` (0-100)
  and its four real inputs (`reliability`, `policy_compliance`, `tool_error_rate`,
  `approval_rate`) and any real anomaly `anomalies` (baseline-comparison call-rate spikes). 404
  `AGENT_NOT_FOUND` for a missing/cross-org agent.
- `GET /costs?window_days=30` (any role) — Cost Center. `total_cost`, `by_agent`, `by_tool` (all
  real, from `events.payload->>'cost'`), and `estimated_savings_from_policy_enforcement` — an
  estimate (a blocked call never runs, so never has a real cost) built from this org's own real
  average cost per `(agent, tool)` pair, not a guessed number.
- `GET /command-center?window_days=1` (any role) — Command Center. `agents_total`/
  `agents_healthy` (an agent counts as unhealthy if it currently has an `OPEN` circuit breaker,
  read live from Redis), `availability_pct` (real `CallCompleted / (CallCompleted + CallFailed)`
  ratio — a reliability signal, not literal infra uptime), `last_incident_at` (most recent
  `CallBlocked`), and `recent_activity` (last 15 allow/block/pending-approval decisions). Polled
  by the frontend, not WS-pushed — a new org-wide broadcast channel is out of scope for this
  endpoint (the existing WS fan-out is per-agent).

## Realtime API

### `WS /live/{agent_id}`
Served by the aggregator. Frontend subscribes here for live graph deltas while an agent is
actively running — pushed via the aggregator's Kafka consumer (U3, v2 upgrade, replacing v1's
Postgres LISTEN/NOTIFY) fanned out through Redis pub/sub (U11, v2 upgrade, `docs/adr/ADR-008`) so
any WS gateway instance can deliver to any connected client, not only the one that happened to
process the underlying Kafka message. No polling on either side.

Multiple updates to the same node arriving within a short coalescing window (default 100ms,
`WS_BATCH_WINDOW_SECONDS`, tunable) collapse to just the latest — a burst of rapid status changes
for one node is delivered as its final state once the window flushes, not as N separate messages.
`node_added`/`edge_added` are structural facts and are never coalesced. The wire format itself is
unchanged either way — still one JSON object per `send_json()` call, coalescing only reduces
message *count* during a burst, never message *shape*.

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
