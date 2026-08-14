# ADR-010: Event table partitioning strategy and retention window

## Status
Accepted

## Context
`events` is append-only and grows forever — every tool call, every policy decision, every outcome.
UPGRADE_ARCHITECTURE.md §11 asks for monthly partitioning "now rather than retrofitting under
production load," plus an explicit, defended retention window, plus connection pooling and query
timeouts. This ADR covers all three (partitioning, retention, and the pooling/timeout piece) since
they're one coherent "database evolution" decision, not three independent ones — ADR-011 covers
object storage separately, since that's a genuinely distinct mechanism (large-payload offloading)
that retention happens to use as its archival destination.

## Options considered (partitioning approach)
1. **Rename-and-rebuild** (chosen): rename the existing table aside, create a new
   `PARTITION BY RANGE (created_at)` table with the identical shape, copy every row across, recreate
   triggers/indexes on the new parent (auto-inherited by every partition since Postgres 11 — no
   per-partition definitions needed), drop the old table. The only approach available at all —
   Postgres cannot convert an existing regular table into a partitioned one in place.
2. **Partition from day one, accept a breaking schema reset.** Rejected: this system already has
   real accumulated data (local dev, and eventually any real deployment) that a "start over" approach
   would discard. The migration is written to work correctly with existing data specifically because
   that's the realistic scenario, not just CI's always-empty case — verified directly against a local
   database with 36,719 pre-existing rows before this ADR was written, not merely asserted correct.

## Real constraints hit while writing the migration (not assumed away)
- Postgres requires every UNIQUE/PRIMARY KEY constraint on a partitioned table to include the
  partition key. `event_id PRIMARY KEY` becomes `PRIMARY KEY (event_id, created_at)`; `UNIQUE
  (trace_id, sequence_number)` becomes `UNIQUE (trace_id, sequence_number, created_at)`. Functionally
  unchanged in practice (`event_id` is a fresh `gen_random_uuid()` per row already; a
  `trace_id`/`sequence_number` pair is assigned once with one fixed `created_at`), but a real
  constraint-shape change worth stating rather than silently absorbing.
- `RENAME TABLE` does not rename its indexes — every index-backed name (the primary key, the unique
  constraint, the plain `events_agent_id_created_at_idx`) had to be explicitly renamed on the old
  table first, or the new table's identically-named indexes would collide (index names share the
  schema-wide namespace with tables, unlike CHECK/FOREIGN KEY constraint names, which only need to be
  unique per-table). Caught on the first migration attempt (`DuplicateTableError`), fixed, re-verified.

## Decision
A `DEFAULT` partition (`events_default`) as a safety net for anything outside the explicit named
ranges, plus twelve named monthly partitions covering all of 2026. `bastion_next_sequence_number`
(unchanged, still queries by `trace_id` with no date filter — correct, just without partition-pruning
benefit for that one function, since a trace's events essentially never span a month boundary in
practice) and the append-only triggers are defined once on the parent, inherited automatically.

**Retention: 90 days hot in Postgres**, then archived to object storage (ADR-011) and detached —
picked and defended, not left as a placeholder: long enough to cover the realistic window this
system's traces actually get investigated in (an incident review, a support escalation, a billing
dispute over a specific tool call), short enough to bound an append-only table's storage growth with
no other cap. `retention.py`'s `run_retention_sweep` is a callable maintenance operation
(`python -m bastion_interceptor.retention`), not an automatically-scheduled job — no scheduler
infrastructure (cron, a k8s CronJob, Celery beat) exists anywhere in this project, and adding one is a
deployment-topology decision explicitly out of scope here, same reasoning as PgBouncer below.

**Connection pooling**: `asyncpg.create_pool` (already in place since v1) is the pooling layer this
system actually uses — a PgBouncer-style proxy in front of Postgres mainly matters at a scale where
many *separate application instances* each hold their own pool, and no load test in this codebase
(U13, not yet run) has shown that's a real bottleneck. Not added speculatively; flagged as a real,
deferred decision rather than silently substituted with "we already pool in-process, so this is done."
**Query timeouts**: `command_timeout` added to every `asyncpg.create_pool` call (interceptor's two
pools, aggregator's one) — a genuinely new protection, not already covered by anything: a hung query
can no longer hold a connection (and, transitively, exhaust the pool under load) forever.

## Consequences
- Partition pruning works and is proven, not assumed: `EXPLAIN (FORMAT JSON)` on a date-range query
  confined to one month's bounds shows only that partition in the plan — not its neighbors, not the
  default partition (`interceptor/tests/test_events_partitioning.py`).
- Archival is real and destructive, verified before data leaves Postgres: `archive_and_detach_partition`
  uploads every row, verifies the uploaded byte count, only then detaches and drops — proven end to
  end against a synthetic 2020-dated partition, including confirming the dropped partition's data
  becomes genuinely unqueryable through `events` while remaining fully readable from its object
  storage archive.
- `bastion_ensure_events_partition` (called by the retention sweep before archiving anything) means a
  new calendar month never arrives without its partition already existing — the forward-looking half
  of "plan for it now."
- No read replica in this ADR at all — UPGRADE_BUILD_PLAN.md's U10 is explicit that a replica only
  gets added after U13's load test shows primary saturation, not speculatively; correctly out of
  scope here.

## Failure modes
A retention sweep interrupted mid-archival (crash between `put_object` succeeding and the
`DETACH`/`DROP` transaction): the archive object already exists in full; re-running the sweep
re-uploads the same content to the same content-independent key (partition name, not content hash —
unlike payload objects, ADR-011) and completes the detach/drop — idempotent by construction, not
"probably fine." A retention sweep run against a partition that's already been archived and dropped:
`list_partitions_older_than` simply won't find it anymore (it's not in `pg_inherits` for `events`), a
clean no-op. Object storage unreachable during a sweep: `put_object`/`head_object` raise, the
transaction wrapping detach/drop never runs — the partition stays attached and hot, exactly as if the
sweep had never been attempted; no partial, silently-inconsistent state is possible.
