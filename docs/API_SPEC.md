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
// Response (pending approval — long-poll up to N seconds, then timeout)
{
  "span_id": "uuid",
  "decision": "pending_approval",
  "approval_request_id": "uuid",
  "poll_url": "/api/v1/approvals/{id}"
}
```
Auth: `Authorization: Bearer <agent api key>`

### `GET /approvals/{id}`
Poll for resolution of a pending approval (SDK long-polls this, or subscribes via webhook).

## Human/dashboard API

### Auth
- `POST /auth/login` — email/password → access + refresh token
- `POST /auth/refresh` — refresh token → new access + refresh token pair (rotation, see AUTH.md)
- `POST /auth/logout` — revoke current refresh token family

### Agents & Policies
- `GET /agents` / `POST /agents` / `GET /agents/{id}`
- `GET /policies` / `POST /policies` (creates new version) / `POST /policies/{id}/activate`

### Approvals
- `GET /approvals?status=pending` — queue for the approver UI
- `POST /approvals/{id}/approve`
- `POST /approvals/{id}/deny`

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
