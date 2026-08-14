# ADR-018: Authorization chain reuses the tool-call policy evaluator (unlisted, added U7)

## Status
Accepted

## Context
UPGRADE_ARCHITECTURE.md §9 asks for an explicit `Subject → Role → Resource → Action → Policy`
evaluation chain for human-authorization decisions (e.g. "can approver X approve a $250 payment for
agent Y"), stating directly: "reuse the policy engine's evaluation mechanics for both — one
evaluator, two rule sets." Not in `ADR_INDEX.md`'s required list, but a genuinely non-obvious design
decision worth recording per that file's own "add new ADRs as new non-obvious decisions get made"
instruction — same reasoning as ADR-017 for U1.

## Options considered
1. **Literal reuse**: an authorization policy is a normal `policies`/`policy_sets` row — same table,
   same versioning, same `POST /policies` + `POST /policies/{id}/activate` endpoints, same in-memory
   `PolicyCache` — looked up by a reserved well-known name per org (`__bastion_authorization__`)
   instead of via `agents.default_policy_set_id`. `policy_engine.evaluate()` is called with
   `tool_name=action`, `args=resource` — everything downstream is the exact same code a tool-call
   decision goes through. **Chosen.**
2. **A second, parallel evaluator** for authorization rules, with its own DSL/table/endpoints.
   Rejected: directly contradicts §9's explicit "one evaluator, two rule sets" instruction, and would
   duplicate the safe-eval condition walker, the match/limits logic, and the caching machinery for no
   real gain — an authorization decision ("is this action, given this resource, allowed") is
   structurally identical to a tool-call decision ("is this tool call, given these args, allowed").
3. **A new column/table distinguishing "kind" of policy_set** (`tool_call` vs `authorization`),
   requiring a migration. Rejected in favor of the reserved-name convention: no schema change, no new
   endpoint, and every piece of existing tool-call-policy machinery (versioning, optimistic
   concurrency from U4, hot-reload from U5's reconciliation loop) works for authorization policies
   for free, with zero new code to keep in sync.

## Decision
`interceptor/authorization.py`'s `check_authorization(org_id, action, resource)` looks up the org's
policy_set named `AUTHORIZATION_POLICY_SET_NAME` and calls `policy_engine.evaluate()` on it exactly
as `/intercept` does for tool calls. No authorization policy configured (the default for every org,
including all of v1's): safe default `allow`, matching `evaluate(None, ...)`'s existing behavior —
this is an *additional* restriction layer on top of the existing RBAC role check
(`require_approver`), never a replacement for it. Wired into `main.py`'s `_resolve_approval`: before
resolving an approve/deny, the underlying call's `CallAttemptedPayload` (`tool_name`, `args`) is
fetched and folded into `resource` alongside the caller's own `role`, then checked; a non-`allow`
decision returns `403 AUTHORIZATION_DENIED` with the evaluator's own `reason` — the "Why?"
explanation, in the identical shape a tool-call block's `reason` already uses, since it's the same
`Decision` object either way.

## Consequences
- Zero new API surface: an admin configures an authorization policy through endpoints that already
  exist, with a DSL (`match`/`action`/`condition`/`limits`) they already know from tool-call policies —
  `match.tool` means "action" in this context, `args` means "resource."
- Every correctness property already built for tool-call policies — U4's optimistic-concurrency
  versioning, U5's reconciliation-loop convergence guarantee — applies to authorization policies
  automatically, with no separate implementation to keep consistent.
- The reserved-name convention (`__bastion_authorization__`) is a soft constraint, not enforced by a
  DB constraint or reserved-word check on policy names — an org could theoretically name a real
  tool-call policy set that exact string and collide. Accepted as an acceptable simplification given
  the name's deliberately unlikely/namespaced shape; a hard reservation (e.g. a `CHECK` constraint)
  would be a reasonable follow-up if this ever proves to matter in practice.

## Failure modes
No authorization policy configured: `check_authorization` returns `allow` immediately (a single
Postgres lookup for the policy_set id, cache miss otherwise) — never blocks, never adds latency
beyond that one lookup on the approve/deny path (not the `/intercept` hot path, so CLAUDE.md rule #4
doesn't directly bind here, but the cost is negligible regardless). Underlying call's
`CallAttemptedPayload` missing or malformed (shouldn't happen — every span has exactly one): `resource`
falls back to just `{"role": user.role}`, and any authorization rule referencing `amount`/`tool_name`
simply won't match (the safe-eval walker treats a missing key as `None`, same behavior as tool-call
conditions already have for a missing `args` key) — degrades to "rule doesn't fire," never a crash.
