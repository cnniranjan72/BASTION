# ADR-016: Optimistic concurrency for policy edits (version check vs alternative)

## Status
Accepted

## Context
UPGRADE_ARCHITECTURE.md §5 specs optimistic concurrency for policy edits as an in-place
`UPDATE policies SET definition = $1, version = version + 1, updated_at = now() WHERE id = $2 AND
version = $3`, returning 409 on zero rows affected. v1's actual `policies` table is not that shape:
`create_policy` (`interceptor/db.py`) already only ever INSERTs — every edit becomes a brand new
row (`UNIQUE (policy_set_id, version)`), and the previous row is never mutated. This is intentional,
not an oversight: `DATA_MODEL.md` states outright "policies are versioned, never edited in place,"
and `test_create_policy_does_not_mutate_previous_version` locks the guarantee in as a passing test
predating this phase. §5's literal SQL draft directly conflicts with that design — there is no
`updated_at` column, and `id` identifies one immutable version, not an editable policy "slot."

Separately, this conflict surfaced a second, real, pre-existing bug: `create_policy` already reads
`MAX(version)` then INSERTs `MAX+1` inside one transaction, but two concurrent callers can both read
the same `MAX` before either commits — the loser's INSERT then hits the `UNIQUE (policy_set_id,
version)` constraint and raises an unhandled `asyncpg.exceptions.UniqueViolationError`, which
FastAPI's default exception handling would surface as a raw 500, not a clean error. This existed in
v1 already; U4 is the first phase to name and fix it.

## Options considered
1. **Build UPGRADE_ARCHITECTURE.md §5's literal in-place-UPDATE endpoint**, on a *new* mutable table
   or a new mutable column set bolted onto `policies`. Rejected: this either breaks the append-only
   guarantee `test_create_policy_does_not_mutate_previous_version` already locks in, or requires
   maintaining two parallel policy representations (immutable history + a separately mutable
   "current" pointer) for no benefit — the append-only model already gives free history/audit trail
   that an in-place edit would destroy.
2. **Reinterpret optimistic concurrency for the immutable-versioned-row model** (chosen): a
   `based_on_version` field on `POST /policies`, checked against the actual current latest version
   for that `policy_set_id` inside the same transaction as the insert. A stale `based_on_version` (or
   a race that slips past the initial check) is rejected with 409, exactly as §5 intends — just
   expressed as "don't let a create silently land past a version its own author never saw," not as an
   in-place mutation guard.
3. **Do nothing beyond catching the pre-existing `UniqueViolationError` and mapping it to 409**,
   without adding `based_on_version` at all. Rejected: without the caller stating what version they
   started from, the server can only detect a race that already happened to collide on the exact
   same `next_version` number — it can't detect the more common case of a caller editing a version
   that's already several revisions stale, which silently "succeeds" (creates a technically-valid
   next version) while discarding the fact that the edit was based on outdated content. `based_on_version`
   is what makes this genuinely *optimistic* concurrency rather than a lucky-collision catcher.

## Decision
`CreatePolicyRequest` gains `based_on_version: int | None = None` (`shared/policy_api.py`) —
optional, so every caller predating U4 keeps its exact old behavior (blind append, no conflict
detection). When supplied, `create_policy` (`interceptor/db.py`) compares it against
`SELECT COALESCE(MAX(version), 0) FROM policies WHERE policy_set_id = $1` inside the same
transaction as the insert; a mismatch raises `PolicyVersionConflict` before any write happens. The
narrow remaining race (two callers who both pass the same, still-current `based_on_version`,
concurrently) is closed the same way ADR-005 already established for idempotency — the Postgres
`UNIQUE (policy_set_id, version)` constraint is the real arbiter, not the app-level check on its
own; the loser's `UniqueViolationError` is caught and converted to the identical `PolicyVersionConflict`.
`main.py` maps this to `409 Conflict` with error code `POLICY_VERSION_CONFLICT`.

## Consequences
- Preserves the append-only policy model and its existing test guarantee untouched — U4 adds a
  precondition on *creating* the next version, it does not introduce any in-place mutation anywhere.
- The pre-existing latent 500-on-race bug is fixed as a side effect of the same change, not a
  separate patch — both the explicit staleness check and the constraint-violation backstop route
  through the one `PolicyVersionConflict` → 409 path.
- Policy Studio (U9) can implement "409 → re-fetch → prompt to reconcile" exactly as
  UPGRADE_BUILD_PLAN.md's U4 entry describes, using `based_on_version` as the version it round-trips.
- `activate_policy` is unaffected — this ADR only concerns creating new policy content, not the
  separate active/inactive toggle, which already has its own DB-level backstop (the partial unique
  index on `active`).

## Failure modes
A caller that omits `based_on_version` gets zero protection, by design — matching ADR-004's
"opt-in idempotency" precedent, this is additive, not a breaking change to the wire contract. A
caller whose `based_on_version` is stale gets a deterministic 409 with no partial write (the INSERT
never runs when the pre-check fails; when it's the constraint backstop that fires, the whole
transaction rolls back, so a freshly-auto-created `policy_sets` row for a brand-new policy name rolls
back too — the next retry recreates it cleanly). Postgres down: `create_policy`'s transaction fails
outright, same fail-closed behavior as any other write in this system.
