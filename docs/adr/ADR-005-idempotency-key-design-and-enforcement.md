# ADR-005: Idempotency key design and enforcement

## Status
Accepted

## Context
Given ADR-004's decision to deduplicate at the interceptor, the key itself needs: a scope (what makes
two calls "the same"), a storage mechanism that's correct under real concurrency (not just sequential
retries), and a defined behavior for callers racing on the same key simultaneously — not just
one-after-another retries.

## Options considered
1. **Application-level check-then-insert** ("does a row exist? if not, insert"). Simple, but a classic
   TOCTOU race under real concurrency — two concurrent requests can both pass the check before either
   inserts, both proceed to evaluate and execute. Rejected: this is exactly the bug idempotency exists
   to prevent, just moved one layer down.
2. **Postgres unique constraint as the arbiter** (chosen): `UNIQUE (agent_id, idempotency_key)` on a
   dedicated table. Concurrent identical requests race on the `INSERT ... ON CONFLICT DO NOTHING`
   itself — the database guarantees exactly one wins, no window for two to both believe they're first.
3. **Redis `SETNX`-based locking.** Faster, but Redis is explicitly ephemeral acceleration in this
   architecture (§4.2) — using it as the sole arbiter for a correctness-critical guarantee would make
   idempotency itself best-effort if Redis restarts mid-window. Postgres, already the durable source of
   truth for everything else, is the correct place for this too.

## Decision
- **Scope**: `UNIQUE (agent_id, idempotency_key)` — not `(agent_id, trace_id, span_id, idempotency_key)`
  as originally drafted in UPGRADE_ARCHITECTURE.md §3. `span_id` is minted *by* the interceptor on the
  winning attempt, not supplied by the caller — it can't be part of the key a retry reuses to find that
  same row, since a retry doesn't know it in advance. `trace_id` is already effectively fixed for a
  given logical call (the SDK holds it in `SpanContext` for the whole call), so including it in the
  key would be redundant with `agent_id` + `idempotency_key` alone providing sufficient scope. `agent_id`
  bounds the uniqueness per caller identity (matching how agent API keys already scope everything else)
  without needing a separate `org_id` dimension, since every agent belongs to exactly one org.
- **Storage**: `idempotency_keys` table, `status` starts `'pending'` on the winning `INSERT ... ON
  CONFLICT DO NOTHING RETURNING *`; losers of the race (`NULL` returned) poll the same row for up to
  2 seconds waiting for `status = 'completed'` and `response_body` to be populated, then return that
  stored response verbatim instead of evaluating anything themselves.
- **What's cached**: the full serialized `InterceptResponse` (allowed/blocked/pending_approval, all
  three) — a retry of a call that resulted in `pending_approval` correctly gets back the *same*
  `approval_request_id`/`poll_url`, not a new approval request.

## Consequences
- Exactly one policy evaluation and exactly one `CallAttempted`/decision event pair per idempotency
  key, proven directly by the milestone test (`interceptor/tests/test_idempotency.py`): 5 concurrent
  identical requests, exactly one `CallAttempted` event exists afterward, all 5 responses share one
  `span_id`.
- No idempotency key supplied → no row, no dedup, no behavior change from v1 — additive, not a breaking
  change to the wire contract.

## Failure modes
A request that never supplies an idempotency key gets zero protection, by design (see ADR-004). A
request whose reservation row never completes (process crash mid-evaluation) leaves that specific key
permanently unusable for a fresh attempt under the current design — no reclaim/expiry logic exists yet.
Acceptable for v2's initial cut since idempotency keys are meant to be single-use per logical call, not
long-lived identifiers a caller would need to retry indefinitely into the future; a TTL-based reclaim
policy is a reasonable follow-up if it proves necessary under real load, not added speculatively here.
