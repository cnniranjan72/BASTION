# ADR-021: Real metric definitions for U16's supporting surfaces

## Status
Accepted (unlisted — U16, v2 upgrade; not in `ADR_INDEX.md`'s required list, following the
ADR-017/018/019/020 precedent for a non-obvious decision worth recording anyway)

## Context
FRONTEND_V2.md's 7 supporting surfaces (Command Center, Trace Explorer, Approval Center, Threat
Center, Agent Health, Cost Center, command palette) each cite illustrative numbers in the spec's own
mock text — "99.97% availability", "tool-call frequency increased 4.7× over baseline", "estimated
savings from policy enforcement". None of these map onto a single existing column or metric this system
already tracks; each needed a real definition decided and documented, not silently approximated or
faked, per FRONTEND_V2.md's own non-negotiable rule ("If a screen can't be wired to something real yet
... build the screen after its backend dependency, not before") and CLAUDE.md rule #3.

## Decisions

**"Threats" (Threat Center)**: no prompt-injection-specific detector exists anywhere in this codebase —
the policy engine blocks calls that match a configured rule, full stop. "Threats" is therefore defined
as blocked calls: `blocked_calls_total`, `top_violated_policies` (grouped by the real `policy_id` on
each `CallBlocked` event's `PolicyDecisionPayload`), and a daily `timeline`. This is what the system
actually enforces against; it does not claim to detect adversarial *intent*, only policy violations.

**"Availability" (Command Center)**: no uptime-history mechanism exists anywhere in this system (no
heartbeat log, no synthetic monitoring) to report literal infra uptime honestly. Redefined as the real
call-success rate over a rolling window: `CallCompleted / (CallCompleted + CallFailed)`, org-wide. This
is a genuine reliability signal derived from real event data, clearly distinct from — and not presented
as — infrastructure/process uptime.

**"Agents healthy" (Command Center)**: an agent counts as unhealthy if it currently has at least one
`OPEN` circuit breaker (`interceptor/circuit_breaker.py`'s real Redis state,
`bastion:breaker:{agent_id}:{tool_name}:state`). The aggregator reads this directly from the same Redis
instance rather than inventing a synthetic health flag — real, current, distributed state, same
philosophy as ADR-020's propagation-status panel.

**"Last incident"**: the most recent `CallBlocked` event's timestamp, org-wide. The most recent real
signal that something was actually stopped, not a separately-tracked "incident" concept this system
doesn't otherwise have.

**Agent health score**: FRONTEND_V2.md asks for "reliability, policy compliance, tool error rate,
approval rate, cost efficiency" without specifying weights or a formula — a decision this ADR makes
explicitly rather than leaving implicit in code:
- `reliability` = `CallCompleted / (CallCompleted + CallFailed)` for that agent.
- `policy_compliance` = `1 - (blocked / calls_total)` — the fraction of the agent's own call attempts
  that didn't get blocked. Framed descriptively (how often this agent's behavior stays within policy),
  not as a judgment about the agent's "goodness."
- `tool_error_rate` = `CallFailed / calls_total`.
- `approval_rate` = `CallPendingApproval / calls_total` — reported as-is, not scored as
  better-or-worse (needing approval is a policy configuration choice, not a defect).
- `cost_efficiency`: this agent's average cost per completed call vs. the org-wide average across all
  agents in the same window; `1.0` at parity, higher when this agent is cheaper than the org average.
- `health_score` (0-100) = `100 × mean(reliability, policy_compliance, 1 - tool_error_rate,
  min(cost_efficiency, 1.5) / 1.5)` — `approval_rate` is surfaced but deliberately excluded from the
  composite score itself (see reasoning above: it isn't a health signal on its own), and
  `cost_efficiency` is capped at 1.5× the org average before normalizing so one unusually cheap agent
  can't single-handedly saturate the score.

**Anomaly flags**: "tool-call frequency increased N.N× over baseline" is computed for real — this
agent's own `CallAttempted` count in the last 24h, divided by its own daily average over the *preceding*
7 days (today excluded from the baseline so a spike doesn't dilute the number it's being compared
against). A flag is emitted only when the ratio is ≥ 2.0× and the baseline is non-zero (a baseline of
zero calls makes any activity technically "infinite×", which is a divide-by-zero artifact, not a real
anomaly worth surfacing).

**"Estimated savings from policy enforcement" (Cost Center)**: a blocked call never runs, so it never
has a real recorded cost — there is no way to know its *actual* cost. Estimated as: for each
`(agent_id, tool_name)` pair, that pair's own real average cost per completed call in this org × that
pair's blocked-call count, summed across all pairs. Built from this org's own real historical cost data,
not a global/guessed number — but still an estimate by construction, and labeled as such in the API
response and every UI surface that shows it.

## Consequences
Every number the 7 supporting surfaces show traces back to a real query over `events`/
`trace_summaries`/`policies`/`agents`/Redis circuit-breaker state — never a hardcoded or randomly
generated figure. The tradeoff, stated plainly: several of these are *estimates* or *redefinitions* of
FRONTEND_V2.md's illustrative language, not literal implementations of it (there is no "prompt-injection
pattern" field, no infra uptime tracker, no per-blocked-call actual cost). Each redefinition is chosen to
be the closest honest real substitute, not merely "something to put in the box."

## Failure modes
If a future phase adds real uptime/synthetic monitoring, a real prompt-injection classifier, or captures
an estimated cost at block-time (before the real call would have executed), each of these definitions
should be revisited toward the more literal reading of FRONTEND_V2.md's spec text — this ADR's shape is
forward-compatible with that (each is one query/formula to swap, not a UI redesign).
