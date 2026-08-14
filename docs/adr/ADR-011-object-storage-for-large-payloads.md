# ADR-011: Object storage for large payloads, threshold and content-addressing

## Status
Accepted

## Context
UPGRADE_ARCHITECTURE.md §12: tool-call payloads (large tool responses, documents an agent read, etc.)
don't belong in Postgres rows. Small structured data (amounts, tool names, decisions) stays inline;
anything above a size threshold gets offloaded to S3-compatible object storage, with `events.payload`
holding a small pointer `{"storage": "s3", "uri": "...", "hash": "...", "size_bytes": ...}` instead.

## Options considered
1. **MinIO locally, any S3-compatible endpoint via `aioboto3`** (chosen) — matches
   UPGRADE_ARCHITECTURE.md §18's stack table entry exactly ("Object storage | S3-compatible"), and
   the same code works against real AWS S3 in a real deployment with only a different `endpoint_url`.
   `aioboto3` (an async wrapper over `boto3`/`aiobotocore`) matches this codebase's async-everywhere
   style, the same reasoning `aiokafka` was chosen for in U3.
2. **Store large payloads directly in Postgres as `bytea`/`jsonb`, no separate service.** Rejected —
   this is precisely the anti-pattern §12 exists to avoid: large blobs in Postgres rows bloat table
   size, slow down every full-table operation (backups, replication, vacuum), and get zero benefit
   from content-addressed dedup.
3. **A local filesystem path instead of an object store.** Rejected: doesn't generalize to any real
   multi-instance deployment (the interceptor is explicitly stateless/horizontally scalable per
   ARCHITECTURE.md §2.2 — a local file wouldn't be visible to a different replica that later needs to
   read it), and would need its own bespoke migration path to S3 later instead of using the same
   interface from the start.

## Decision
Threshold: 8KB (`config.object_storage_payload_threshold_bytes`), matching §12's own example figure —
small enough that the overwhelming majority of events (a few hundred bytes of tool name, args,
decision) never touch object storage at all, large enough that offloading isn't triggered by
routine-sized payloads. Content-addressing: the object key is the payload's own SHA-256 hex digest
(`payloads/{hash}.json`) — genuine dedup, not just a design intention: `upload_if_large` does a
`head_object` check before any `put_object`, so an identical payload (the exact shape a retried or
repeated tool call produces) is never uploaded twice, proven directly
(`test_identical_large_payload_deduplicates_to_the_same_object`).

`upload_if_large` is wired into `db.insert_event` — every write goes through it, both the `events` row
and its `outbox_events` copy always carry identical payload content (pointer or not), so a Kafka
consumer sees exactly what Postgres has. `resolve_payload` is wired into `db.get_call_attempted_payload`
only. **Scope, stated explicitly rather than silently assumed complete**: `get_events_for_trace`'s
rows stay raw, unresolved `asyncpg.Record`s (`payload` is one field among several there, not cleanly
wrappable into a resolved dict without a larger interface change to every caller that currently does
`event["payload"]`-style access), and the aggregator's `fold_events_to_graph` (a separate service
entirely) still reads `payload` as plain inline JSON. A payload large enough to be offloaded flowing
into either of those not-yet-retrofitted consumers would see the pointer object instead of the real
data — a real, bounded gap, not a claim this is fully rolled out everywhere in this pass.

## Consequences
- The write and read halves are proven together, not just as isolated units: a genuinely large
  `CallAttempted` payload written through `db.insert_event` is confirmed to land in Postgres as a
  small pointer (not the original ~20KB blob), and `db.get_call_attempted_payload` is confirmed to
  transparently return the full original content back — the actual round-trip the milestone test asks
  for (`test_large_call_attempted_payload_round_trips_through_insert_event`).
- Retention's archival destination (ADR-010) reuses this same object storage instance/bucket, under a
  different key prefix (`archives/` vs `payloads/`) — one piece of infrastructure serving two related
  but distinct purposes (individual large payloads vs. whole detached partitions).
- MinIO's data volume (`miniodata`, `infra/docker/docker-compose.yml`) is local-dev/CI-only, same
  posture as every other local credential and volume in this stack (plain `bastion`/`bastion123`
  credentials, not production-secret-grade) — explicitly not a claim this is production-hardened
  storage.

## Failure modes
Object storage unreachable during a write: `upload_if_large` catches any exception from the
head/put attempt, logs it, and returns the original payload unmodified — the event still gets
written, just inline instead of offloaded, exactly as if it had been under the threshold all along
(same "fail open, never block non-essential work" posture CLAUDE.md rule #4 already establishes for
U6's circuit breaker/limits checks). An oversized row in Postgres during an object storage outage is
an accepted, temporary cost; a failed event write over what's meant to be a storage-tier optimization
is not. Object storage unreachable during a *read* (`resolve_payload`): **not** given the same
treatment — `get_call_attempted_payload` still raises rather than degrading, a real, deliberately
un-fixed gap in this pass (the "safe" fallback for a read failure is less obvious than for a write:
returning a placeholder risks masking a genuine data-availability problem from a caller that needs the
real content, e.g. the circuit breaker's `tool_name` lookup). Flagged honestly rather than assumed
symmetric with the write-side fix, given how rarely the threshold is crossed and how narrow the
resulting blast radius is (only affects a payload that was both large enough to offload *and* whose
storage backend is down at read time).
