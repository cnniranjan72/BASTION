# ADR-004: At-least-once delivery + idempotent processing (effective exactly-once outcome)

## Status
Accepted

## Context
`POST /intercept` is called over a real network. A client can time out waiting for a response that
did in fact commit server-side, and retry. Without a way to recognize "this is the same logical call
retried, not a new one," a retried payment charge, a retried DB delete, or a retried email send can
happen twice — UPGRADE_ARCHITECTURE.md §3's stated problem.

## Options considered
1. **True exactly-once delivery.** Not achievable over an unreliable network without the receiver
   doing deduplication anyway (the well-known distributed-systems result) — pursuing this directly is
   the wrong framing, not just hard.
2. **At-most-once** (never retry). Simpler, but a transient network blip then silently drops a call
   that might have succeeded — wrong failure mode for a security/governance product where "did this
   call happen or not" must be knowable.
3. **At-least-once delivery + idempotent processing at the interceptor** (chosen). The SDK retries on
   transport failure; the interceptor recognizes a repeated idempotency key and returns the original
   decision instead of re-evaluating. Delivery can happen more than once; the *effect* — one policy
   evaluation, one decision, one downstream execution — happens exactly once.

## Decision
At-least-once delivery (SDK retries transport failures — connection errors, timeouts — up to 3
attempts with backoff) combined with idempotent processing (interceptor deduplicates by
`(agent_id, idempotency_key)`, unique-constrained in Postgres). Together these produce an effective
exactly-once *outcome* for the caller, without requiring the downstream tool API itself to be
idempotent. See ADR-005 for the key's concrete storage/enforcement design.

Precisely which failures are safe to retry matters: only transport-level failures (`httpx.TransportError`
— connection reset, timeout, no response received) are retried. An HTTP error response (4xx/5xx) is a
real decision that already happened and reached the client; retrying it blindly would not be a retry
of an *ambiguous* outcome, it would be ignoring a known one.

## Consequences
- Every real caller through the SDK gets this for free — the idempotency key is generated once per
  logical `call()` invocation and reused across all retry attempts, transparently.
- A raw HTTP caller bypassing the SDK does not get this protection unless it supplies its own
  `idempotency_key` — documented as an explicit, deliberate degrade (see `InterceptRequest`'s field
  docstring), not a silent gap.
- Adds one DB round trip (an idempotency-table lookup) to every `/intercept` call that supplies a key,
  even on the non-retry path — accepted, since it replaces what would otherwise be an unbounded
  duplicate-execution risk.

## Failure modes
If the process that reserved an idempotency key crashes before completing it, concurrent/later callers
polling for its result (see ADR-005) time out after 2 seconds and receive `503
IDEMPOTENT_REQUEST_IN_PROGRESS` rather than hanging indefinitely or silently re-evaluating — a clean,
documented degraded state, not a wedge.
