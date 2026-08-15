# ADR-020: Policy simulator and propagation-status endpoint scope

## Status
Accepted (unlisted — U15, v2 upgrade; not in `ADR_INDEX.md`'s required list, following the ADR-017/018/019 precedent for a non-obvious decision worth recording anyway)

## Context
FRONTEND_V2.md's Policy Studio requires two things the backend didn't have: a simulator ("paste a hypothetical tool call ... walk through the actual evaluation chain ... using the real policy engine, not a UI-only approximation") and a propagation-status panel ("Policy v14 active across 4/4 interceptors ... proves the UI is wired to real distributed state, not faked"). Per the project's standing rule against building UI screens against mocked backend state, both needed real endpoints before the UI could be built honestly. Two design questions had no obvious single answer:

1. The simulator's spec text explicitly lists "limits check" as part of the chain to walk through — but the real limits mechanism (`limits.check_and_apply_limits`) mutates Redis counters keyed on the real agent (rate-limit consumption, spend accumulation). Should a hypothetical "what if" call actually apply those side effects?
2. The propagation-status spec text ("4/4 interceptors") assumes a multi-replica deployment with some way to know how many replicas exist and what each has cached. No such registry exists anywhere in this codebase — interceptor instances are independent, discovered by nothing.

## Options considered
1. **Simulator actually applies limits/circuit-breaker checks** — most literal reading of "walk through the actual evaluation chain." Rejected: a human exploring "what would happen if agent X called Y" would silently spend that agent's real per-minute/per-day budget and could flip a real circuit breaker, corrupting production state for a diagnostic action. This also can't be undone (Redis INCR isn't reversible after the fact).
2. **Simulator reports configured limits informationally, applies nothing** — shows what the matched rule *would* check (e.g. `calls_per_minute: 2`) without touching Redis. Chosen.
3. **Build a real interceptor service-discovery/heartbeat registry** for propagation status — the literal way to make "4/4 interceptors" true. Rejected as disproportionate: this is real distributed-systems infrastructure (heartbeats, registration, staleness handling) that nothing else in this system needs yet, purely to make one UI panel's example copy literally accurate.
4. **Propagation status reports real state honestly scoped to what's actually knowable** — compares Postgres's real active version against the actual live `policy_cache` of the one instance handling the request, and reports `known_interceptor_instances: 1` rather than fabricating a fleet count. Chosen.

## Decision
`POST /policies/simulate` reuses the exact same `policy_cache.get()` + `policy_engine.evaluate()` call `/intercept` itself makes (never a separate simulated evaluator), and returns the matched rule's `limits` as informational context only — `limits.check_and_apply_limits`/`circuit_breaker.is_open` are never invoked.

`GET /policies/{policy_set_id}/propagation` compares Postgres's real active-version row against this process's real, live in-memory `policy_cache` and reports the comparison honestly, including `known_interceptor_instances: 1` — a true statement about this deployment's actual topology, not a stand-in for a fleet-wide count no mechanism produces.

## Consequences
Both endpoints are genuinely wired to real backend mechanisms (satisfying the non-negotiable rule in FRONTEND_V2.md) without introducing either a state-corruption risk or unbuilt distributed-systems infrastructure. The tradeoff: the simulator can't demonstrate an *actual* rate-limit rejection (only that a limit is configured), and the propagation panel's UI copy has to say something honest like "active on this interceptor" rather than FRONTEND_V2.md's literal example text — a copy decision, made in the frontend, not a backend gap.

## Failure modes
If a future phase adds a real multi-replica registry, `known_interceptor_instances` and the propagation comparison should be extended to aggregate across it — this endpoint's shape (one instance's honest self-report) is forward-compatible with that: the aggregation point can call this same endpoint per instance and sum results, no breaking change needed. If the simulator's limits are later found insufficient (a user wants to see a live count against the real limit), that's a new, explicit feature decision — not a bug in this one, which deliberately never touches that state.
