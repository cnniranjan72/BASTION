# ADR-017: Call state machine — TERMINAL as derived, APPROVED/EXECUTING collapsed into existing events

## Status
Accepted

## Context
UPGRADE_ARCHITECTURE.md §2 draws the call lifecycle as ten boxes, including `TERMINAL` as a state
that `BLOCKED`/`DENIED`/`COMPLETED`/`FAILED` all transition *into*, and `EXECUTING` as a state
distinct from `ALLOWED`. v1's actual event vocabulary (`bastion_shared.events.EventType`) has no
event for "became terminal" and no event for "started executing" — the interceptor never proxies
the real call (ARCHITECTURE.md §8), so it has nothing to mark execution's start with, and recording
blocked/denied/completed/failed already *is* the terminal fact. Implementing the diagram completely
literally would mean inventing two event types with no real trigger, just to satisfy the drawing.

## Options considered
1. **Literal**: add `TERMINAL` and a distinct persisted `EXECUTING`-entry event. Matches the diagram
   exactly; requires two new event types nothing in the system ever actually emits, and a second event
   write on every terminal call for no informational gain — the reader already knows a `CallBlocked`
   event is terminal.
2. **Derived properties, existing events reused** (chosen): `is_terminal(state)` is a pure function
   over the four real terminal states, not a stored value. `ApprovalGranted` maps directly to
   `CallState.ALLOWED` (v1 already treats it as the allowed-equivalent — no separate `CallAllowed`
   follows it). `EXECUTING` stays in the enum and the pure transition table (so the milestone test can
   assert `ALLOWED -> EXECUTING` and `BLOCKED` having zero outgoing edges, satisfying invariant #1
   literally), but `guard_event` applies `ALLOWED -> EXECUTING` as an implicit, always-legal hop the
   instant a `CallCompleted`/`CallFailed` event is checked against an `ALLOWED` span, rather than
   requiring an event that would never be produced.
3. **Drop EXECUTING entirely**: fold `CallCompleted`/`CallFailed` as direct legal successors of
   `ALLOWED`. Simpler, but loses the ability to state invariant #1 ("BLOCKED can never reach
   EXECUTING") as a literal, testable fact about the transition table — the diagram's distinction
   between "allowed" and "actually running" is real even if v1 has no event boundary for it.

## Decision
Option 2. `CallState` has all nine boxes from the diagram as enum values (`APPROVED` excluded — see
below); `is_terminal()` is a derived predicate, not a state; `guard_event()` is the one place that
bridges "the events v1 actually emits" to "the diagram's edges," applying the implicit
`ALLOWED -> EXECUTING` hop explicitly and testably rather than silently.

`APPROVED` (the diagram's `PENDING_APPROVAL -> APPROVED -> ALLOWED` chain) is collapsed: v1's
`ApprovalGranted` event maps straight to `CallState.ALLOWED`. Modeling `APPROVED` as its own state
would require a second event that never gets written, for a transition that's already atomic in
practice (an approver's click resolves directly to "now allowed," per `db.resolve_approval`'s
own atomicity).

## Consequences
- The milestone test (`shared/tests/test_call_state.py`) can assert the full diagram exhaustively —
  every legal edge, every illegal pair (state × state minus legal edges), every terminal state
  rejecting everything — without the module lying about what real events exist.
- `main.py`'s event-writing call sites stay unchanged in *what* they emit; they gain a `guard_event()`
  call before each write, replacing the one ad-hoc check that existed (`/spans/{id}/complete`'s
  manual `event_type not in (...)`) with the same mechanism every other transition now uses.
- If a future event type is ever added between ALLOWED and COMPLETED/FAILED (e.g. a real "execution
  started" signal), `guard_event`'s implicit hop becomes an explicit one — a small, contained change,
  not a redesign.

## Failure modes
`guard_event` raising `IllegalStateTransition` on a legitimate real-world race (e.g. two concurrent
`/spans/{id}/complete` calls for the same span) surfaces as a 409, matching v1's existing behavior for
the equivalent case — no new failure mode introduced, the same illegal-double-completion case that
was already rejected is now rejected by the state machine instead of an ad-hoc tuple check.
