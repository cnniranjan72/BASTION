# ADR-009: Multi-tenant isolation via Postgres RLS + app-layer scoping

## Status
Accepted

## Context
CLAUDE.md rule #7 already requires every query touching `agents`/`policies`/`traces`/`events` to be
scoped by `org_id`, proven by an application-layer test that org A can't read org B's data. That's
necessary but not sufficient (UPGRADE_ARCHITECTURE.md §10): a single missed `WHERE org_id = ...` in
one query is a real data leak, and application-layer discipline alone has no second line of defense
against that mistake. U8 adds Postgres Row-Level Security as that second layer — enforced by the
database itself, unable to be bypassed by a forgotten `WHERE` clause in application code.

## Options considered
1. **RLS policies + a dedicated non-superuser role** (chosen). Every RLS-enabled table gets a policy
   filtering on a per-connection session variable (`app.current_org_id`), and a new Postgres role
   (`bastion_app`) that the application connects as specifically for RLS-protected queries.
2. **RLS policies against the existing connection role alone.** Rejected — and this is the real,
   consequential finding of this phase: the existing `bastion` role is the Postgres bootstrap
   superuser (`POSTGRES_USER` in the official Docker image becomes the cluster's initial superuser).
   Row-Level Security is *unconditionally* bypassed for superusers — not overridable by
   `FORCE ROW LEVEL SECURITY`, not a configuration mistake, a hard Postgres rule. Every policy in
   migration 0010 would be silent, decorative dead code against the connection every other part of
   this system already uses. Confirmed empirically (not just from documentation) before committing to
   this design: a raw query against `agents` via the `bastion` connection returned rows across
   multiple orgs at once, with the exact same policies present on the table.
3. **Application-level tenant-context middleware only** (no DB-level enforcement at all). Rejected
   outright — this is exactly the status quo ante U8 exists to improve on; see Context above.

## Decision
Migration `0010_row_level_security.sql` creates `bastion_app` (idempotently — roles are
cluster-level, not per-database, so a persistent local dev volume could already have it from a prior
run) and enables `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` + a
`USING (org_id = current_setting('app.current_org_id', true)::uuid)` policy on every table that
carries `org_id` directly: `organizations`, `agents`, `policy_sets`, `policies`, `trace_summaries`,
`users`, `api_tokens`, `idempotency_keys`. `interceptor/db.py`'s `Database` gains a *second* pool
(`config.app_database_url`, connecting as `bastion_app`) and an `org_scoped_connection(org_id)`
context manager that sets the session variable via `set_config(..., true)` (transaction-scoped) and
yields a connection from that pool. `db.list_agents` is retrofitted to use it, with the
application-layer `WHERE org_id` clause *removed entirely* — the concrete, load-bearing
demonstration the milestone test proves, not just a standalone mechanism nobody actually calls.

**Real bug found while writing the milestone test, fixed in follow-up migration
`0011_rls_empty_string_guc_fix.sql`**: `app.current_org_id` is a Postgres "placeholder" GUC (a
custom variable not tied to a loaded extension). The first `SET LOCAL`/`set_config(..., true)` call
on a given physical connection registers the placeholder; when that transaction ends, it resets to
an **empty string**, not `NULL` — meaning a *reused* pooled connection (asyncpg pools reuse
connections across requests, exactly the scenario RLS needs to protect against) that later runs a
query without re-setting the context would hit `''::uuid`, a hard cast error, not the intended
fail-closed "zero rows." `NULLIF(current_setting(...), '')` collapses both the true-unset and
reset-to-placeholder-empty-string cases to the same safe `NULL`, which the policy's `= NULL`
comparison correctly treats as "no rows visible" with no error. Caught by
`test_rls_session_with_no_org_context_sees_nothing` failing on its very first run — a real defect in
the initial design, not a hypothetical.

## Consequences
- `agents` (via `list_agents`) is genuinely, provably protected by Postgres itself now, not solely by
  application code — proven directly, including the specific failure mode (a query with no org filter
  at all) the milestone test's wording asks for.
- **Scope, stated explicitly rather than silently assumed complete**: RLS covers only the tables with
  a direct `org_id` column. `events`, `outbox_events`, `approval_requests`, and `refresh_tokens` are
  NOT covered — each would need either a denormalized `org_id` column or a subquery-based policy
  against `agents`/`users`, a real, larger follow-up. Within the covered tables, only `list_agents`
  has been retrofitted to actually route through `org_scoped_connection` — every other query in
  `db.py` (interceptor) and all of `aggregator/db.py` still relies solely on application-layer
  scoping, unchanged from before this phase. This is a deliberate, bounded first rollout, not a
  claim that every query in the codebase is now RLS-protected.
- `bastion_app`'s password (`bastion_app`, matching this project's existing plain dev-credential
  convention like `bastion:bastion`) is dev/CI-appropriate, not production-secret-grade — flagged
  explicitly, same posture as every other credential in this local setup.

## Failure modes
A query via `org_scoped_connection` that forgets nothing works correctly (the normal case, proven by
`list_agents`'s tests). A future retrofit that reuses a pooled `bastion_app` connection *outside*
`org_scoped_connection`'s transaction wrapper (bypassing the `set_config` call) now safely sees zero
rows rather than erroring or leaking cross-org data, thanks to the `0011` fix — proven directly by
`test_rls_session_with_no_org_context_sees_nothing`. The superuser (`bastion`) pool remains completely
unaffected by any of this and continues to see everything, by design — it's still what every
not-yet-retrofitted query uses, unchanged.
