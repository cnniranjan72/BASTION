# ADR-007: Policy distribution — eventual consistency + reconciliation loop

## Status
Accepted

## Context
Each interceptor instance holds an in-memory `policy_id -> compiled rules` cache
(`policy.py`'s `PolicyCache`, keyed by `policy_set_id`) so evaluating a policy on the `/intercept`
hot path never touches Postgres (CLAUDE.md rule #4). v1 already broadcasts changes via Redis
pub/sub (`redis_bus.py`'s `POLICY_UPDATES_CHANNEL`) so every instance hot-reloads with no restart.
Redis pub/sub, though, has no delivery guarantee and no replay: an instance that's briefly
disconnected, mid-restart, or simply drops a message never receives it and has nothing that would
ever tell it to re-check — it serves a stale policy version indefinitely, silently, with no error
anywhere. UPGRADE_ARCHITECTURE.md §6 names this failure case explicitly and asks for "eventual
convergence as an explicit, provable guarantee rather than an assumption."

## Options considered
1. **Periodic reconciliation loop** (chosen): every interceptor instance re-checks its cached
   policy versions against Postgres on a fixed interval, self-healing any drift it finds —
   independent of whether pub/sub is working at all. Bounds the worst-case staleness window to one
   interval, provably, regardless of how the drift happened.
2. **Make Redis pub/sub itself reliable** (e.g. Redis Streams with consumer groups and acknowledged
   delivery, matching Kafka's model). Rejected for this specific gap: it would fix the delivery
   guarantee but the *listener process itself* being down at broadcast time is still an unrecoverable
   miss under any pub/sub-shaped mechanism — nothing replays a stream to a consumer that wasn't
   subscribed yet unless that consumer also does its own catch-up read on startup, which is most of
   the complexity of this ADR's chosen approach anyway, just framed differently. Also would blur
   Redis's role — already explicitly ephemeral acceleration (§4.2) — the same boundary ADR-002 and
   ADR-005 both already decided not to cross.
3. **No reconciliation, restart to recover.** Rejected outright: this makes "stale policy in effect"
   an operational/manual-intervention problem instead of a self-healing one, and directly
   contradicts UPGRADE_ARCHITECTURE.md §6's explicit requirement for a provable convergence bound.

## Decision
`policy_reconciler.py`'s `reconcile_once()` runs a full sweep every
`POLICY_RECONCILIATION_INTERVAL_SECONDS` (default 30s, `config.py`): fetch every currently-active
policy from Postgres (`db.get_active_policies()`, the same query the startup bootstrap already
uses), and for each one, compare its `id` against what this instance's cache holds for that
`policy_set_id` — a mismatch (including "nothing cached at all") triggers the identical
compile-and-`put` the Redis listener's `_reload_policy_set` callback already does, so a self-heal is
indistinguishable from an ordinary hot-reload once applied. The reverse direction of drift is
covered too: any `policy_set_id` this instance still has cached that Postgres no longer lists as
active at all gets evicted (`PolicyCache.cached_set_ids()`, added for this). `PolicyReconciler`
(`start`/`stop`/`_run_forever`) is wired into `main.py`'s `lifespan` alongside — not instead of —
the existing Redis listener: pub/sub stays the fast path (near-instant propagation when it works),
this loop is purely the backstop for whatever it misses.

## Consequences
- A missed pub/sub message is now a bounded-staleness problem, not an indefinite one: worst case is
  one full reconciliation interval, proven directly by the milestone test
  (`interceptor/tests/test_policy_reconciliation.py`), which activates a new version by calling
  `db.activate_policy` directly (bypassing the broadcast entirely) and asserts a running
  `PolicyReconciler` converges the cache within its interval.
- `reconcile_once()` is a plain function, independently callable and tested
  (`test_reconcile_once_heals_drifted_entry_and_reports_count`,
  `test_reconcile_once_evicts_entry_no_longer_active_anywhere`) — the periodic loop is just this
  function called on a timer, not a separate implementation with its own chance to disagree with it.
- A failed sweep (e.g. Postgres transiently unreachable) is caught and logged, never allowed to kill
  the loop — the instance simply keeps whatever it had (correct-if-stale, never wrong) and gets
  another chance next interval.
- Trade-off, accepted deliberately: this is a full sweep over every active policy, not incremental —
  fine at the scale this system operates at (one row per active policy_set, not a large N), but
  would need revisiting if the number of policy sets ever grew large enough for a full Postgres
  fetch every interval to matter.

## Failure modes
Postgres unreachable during a sweep: `reconcile_once()`'s exception propagates up to
`PolicyReconciler._run_forever`'s catch-and-log, the loop is unaffected and retries next interval —
the cache is never corrupted by a failed sweep, only left exactly as stale as it already was. Redis
down entirely (not just a missed message, the whole pub/sub path unavailable): hot-reload propagation
stops working altogether, but this loop keeps converging every instance to the correct state anyway,
just at reconciliation-interval granularity instead of near-instant — the system degrades to "eventually
consistent within N seconds," never "wrong forever." The reconciler task itself crashing (a bug, not
a transient Postgres issue) is the one gap this design doesn't currently self-heal — `_run_forever`'s
`try/except Exception` around the sweep body only guards the *sweep*, not a defect in the loop
control flow itself; not exercised or hardened against beyond that as of U5, since nothing about a
plain `while True: sleep; sweep` loop that's already exception-safe inside its body poses that risk
in practice.
