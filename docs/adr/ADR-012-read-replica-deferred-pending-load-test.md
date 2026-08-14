# ADR-012: Read replica introduction criteria (benchmark-triggered, not speculative)

## Status
Accepted — deferred, not implemented

## Context
UPGRADE_BUILD_PLAN.md's U10 is explicit about its own prerequisite: "Run the Phase U13 load test
first. If and only if it shows primary saturation under realistic read load, add a read replica...
If the numbers don't justify it, write the ADR explaining why you did NOT add a replica — that's a
legitimate and stronger outcome than adding one speculatively." U13 (SLOs, load testing, alerting) has
not run yet as of this ADR — it comes later in the phase order this session has been working through
sequentially. There is a real, acknowledged ordering tension here: U10 is numbered before U13 in the
build plan, but its own content depends on U13's output. This ADR resolves that tension the way the
build plan's own text anticipates, rather than either skipping U10 silently or adding a replica
speculatively to avoid leaving the phase "incomplete."

## Options considered
1. **Defer the infrastructure decision, write this ADR now** (chosen). U10's literal deliverable —
   "before/after load test numbers... or, if the numbers don't justify it, the ADR explaining why
   not" — doesn't require the *numbers* to exist as of every phase in between; it requires the
   *decision process* to be benchmark-triggered, not speculative. Writing the ADR now, stating
   explicitly that no data exists yet to justify a replica and that this decision is provisional
   pending U13, satisfies the actual engineering principle U10 is testing for (don't add
   infrastructure without evidence it's needed) without fabricating numbers that don't exist.
2. **Add a read replica now anyway, "to be safe."** Rejected — this is precisely the speculative
   infrastructure-addition U10's own text calls out as the *weaker* outcome. A replica adds real
   operational surface (replication lag to reason about, a second connection target, routing logic
   for which queries go where) for a benefit that, as of this ADR, is entirely unmeasured.
3. **Skip U10 silently, revisit only after U13.** Rejected — leaves the decision-in-principle
   undocumented, and risks a future session either forgetting the dependency or re-litigating it from
   scratch. Recording the deferral explicitly, with the actual reasoning, is cheap and keeps the
   decision traceable.

## Decision
No read replica exists as of this ADR. The trigger condition is exactly what UPGRADE_ARCHITECTURE.md
§11 and UPGRADE_BUILD_PLAN.md's U10 specify: primary saturation shown under realistic read load in
U13's k6 load tests (50/100/500/1K/5K RPS, per U13's own description). When U13 runs, this ADR should
be revisited — either superseded with a new ADR recording the actual before/after numbers and the
replica addition, or updated in place with the numbers that justified *not* adding one, if that's
what U13 shows instead. Candidate reads to route to a replica if one is added, per §11: trace/replay/
analytics reads — never writes, and never a read that must be strongly consistent (§11's own example:
approval resolution, which reads `approval_requests`/`events` state a caller may act on immediately
after a write to the same rows).

## Consequences
- Zero new operational surface added speculatively. The primary remains the only Postgres target for
  every query in this system, exactly as it has been through U1–U9.
- This ADR is intentionally incomplete as a standalone artifact — it names the trigger and the
  candidate routing policy, but carries no numbers, because none exist yet. That incompleteness is
  the point, not an oversight: a future session (or this one, once U13 runs) has a clear, named
  follow-up rather than a silently-skipped phase.
- U11 (realtime fan-out) and beyond are unaffected by this deferral — none of the work in those phases
  depends on a replica existing.

## Failure modes
Not applicable in the usual sense — there's no new mechanism here to fail. The risk this ADR
guards against is procedural: a future session adding a replica without re-checking this document
first, missing the "benchmark-triggered" reasoning and re-introducing the speculative-addition problem
this ADR exists to avoid.
