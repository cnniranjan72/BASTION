# ADR-015: Circuit breaker thresholds and scope (per-tool vs per-host)

## Status
Accepted

## Context
UPGRADE_ARCHITECTURE.md §7 asks for a standard three-state breaker per `(agent_id, tool_name)` or
per downstream host, "in the interceptor's execution path, right before the real downstream call is
made." That placement assumes the interceptor makes the downstream call — it doesn't. Per
`docs/PROGRESS.md`'s already-documented v1 deviation #2, the SDK calls the real tool client-side; the
interceptor only ever decides allow/block/pending_approval and later learns the outcome via
`POST /spans/{id}/complete`. §8's `limits:` DSL extension has a related but distinct gap: the safe
condition evaluator (`policy.py`'s `ast`-walker) is deliberately stateless and args-only — it has no
mechanism to read cross-call state like a rolling call count or a spend total, so `limits:` can't be
expressed as another `condition:`-style expression.

## Options considered (breaker placement)
1. **Per (agent_id, tool_name), decision-time check + completion-time update** (chosen): the breaker
   is consulted once policy evaluation has already decided a call would otherwise be `allow` — the
   closest equivalent to "right before the real downstream call is made" available in this
   architecture, since that's the last checkpoint before the SDK is told to proceed. Its state is
   updated retroactively from `POST /spans/{id}/complete`, the only channel through which the
   interceptor ever learns a real outcome.
2. **Per downstream host** (the alternative §7 offers). Rejected for this pass: nothing in the
   current data model tracks a "downstream host" as a first-class concept — `args` is a free-form
   dict, and there's no reliable, generic way to extract a host from it across arbitrary tools
   without inventing a new required field every agent would need to populate. `(agent_id, tool_name)`
   is already meaningful and available with zero new required fields.
3. **In-process (per-interceptor-instance) breaker state.** Rejected: would let each replica
   independently keep retrying a downstream the others have already learned is failing — defeats the
   actual point of a shared circuit breaker in a horizontally-scaled deployment.

## Options considered (`limits:` enforcement mechanism)
1. **Separate stateful check, run after the stateless `evaluate()`** (chosen): `policy.py`'s
   `evaluate()` stays exactly as fast/stateless as before, now additionally returning the matched
   rule's `limits` (and its `match.tool`, needed to key `calls_per_minute` correctly for a wildcard
   rule). `limits.py`'s `check_and_apply_limits` then does the real Redis-backed check, called from
   `main.py`'s `_decide_and_record` only when a rule's decision was already `allow`.
2. **Extend the condition evaluator to support Redis reads.** Rejected: breaks the evaluator's core
   safety property (a small, auditable, side-effect-free AST walker) for no real benefit — `limits:`
   is a structurally different kind of rule (aggregate, cross-call) from `condition:` (single-call,
   pure), and conflating them in one field/mechanism would make both harder to reason about.

## Decision
Circuit breaker state (`circuit_breaker.py`) lives in Redis, keyed `bastion:breaker:{agent_id}:{tool_name}:*`
— `state` (CLOSED implicit/absent, OPEN, HALF_OPEN), `failures` (consecutive count, TTL'd so a stale
streak decays), `opened_at`. `is_open()` is checked in `_decide_and_record` right after policy
evaluation; a breaker that's OPEN converts an otherwise-`allow` decision into a `block` with a
reason string, reusing the exact same block-emission code path a policy-blocked call already goes
through. `record_success`/`record_failure` are called from `complete_span` based on
`CallCompleted`/`CallFailed`.

`limits:` (`shared/policy.py`'s `PolicyLimits`) implements, with real working Redis enforcement:
`max_transaction_amount` (pure comparison against `args["amount"]`, the pre-existing convention from
`condition:` examples — no typed "cost" field exists anywhere in the wire contract, confirmed by
survey), `calls_per_minute` (fixed-window `INCR`+`EXPIRE`, doubling as both "per agent" when a rule's
`match.tool` is `"*"` and "per tool" when it names one — same mechanism, the rule's own scope decides
the dimension), and `org_spend_per_day`/`agent_llm_budget_per_hour` (a shared check-then-commit spend
accumulator, differing only in window/key). **Deliberately not implemented**, stated explicitly
rather than silently dropped: a distinct tool-call-count budget (redundant with `calls_per_minute`)
and a runtime/duration budget (not knowable until `CallCompleted`/`CallFailed`, well after the
decision point `limits:` gates — would need a fundamentally different, retroactive enforcement
shape, out of scope for this pass).

## Consequences
- A breaker trip is indistinguishable, from the SDK's perspective, from an ordinary policy block —
  `BastionBlockedError` either way — which is the right behavior: the SDK's `execute()` callback
  never runs in either case, proven directly by the milestone test's `executed is False` assertion.
- Both mechanisms reuse the exact same "reassign `decision` to a block, let existing code handle it"
  pattern, keeping the diff to `_decide_and_record` small and avoiding a second response-building path.
- `_check_and_commit_spend`'s check-then-commit (GET, compare, then `INCRBYFLOAT`) is not atomic — a
  documented, deliberate simplification. A true production hardening would close this with a Lua
  script; not worth the added complexity at this system's current scale.
- HALF_OPEN similarly doesn't restrict itself to exactly one in-flight trial call under genuinely
  concurrent load — every call is let through once HALF_OPEN, whichever outcome resolves first
  decides the next transition. Acceptable for this phase, flagged rather than silently assumed correct.

## Failure modes
Redis unreachable: `main.py` wraps both `_decide_and_record`'s limits/breaker checks and
`complete_span`'s breaker recording in `except redis.RedisError`, failing *open* — log and proceed
as though this phase's checks didn't run, rather than let a Redis outage 500 either the
latency-critical `/intercept` path (CLAUDE.md rule #4) or turn an already-successful, already-durably-
recorded call into a 500 the caller has to handle at completion time. This is a deliberate, explicit
choice to prioritize availability of the core intercept/complete flow over these two protective
mechanisms' own availability, consistent with Redis's ephemeral/non-authoritative role everywhere
else in this system — not covered by a dedicated fault-injection test this phase (would need a way
to simulate Redis being down mid-suite), a real gap in coverage flagged here rather than assumed
adequately proven by the unit-level `try/except` alone. Circuit breaker state lost entirely (Redis
restart): every breaker implicitly resets to CLOSED — a safe default (fails open toward "trust policy
evaluation again"), never a correctness issue, just a reset of learned failure history.
