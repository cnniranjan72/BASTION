"""Explicit call-lifecycle state machine — UPGRADE_ARCHITECTURE.md §2 / ADR-017.

The single correctness artifact every call-status transition in the
interceptor goes through, replacing the scattered ad-hoc checks v1 had
(e.g. `/spans/{id}/complete`'s manual `event_type not in (...)` check).
Enum values and edges match the §2 diagram exactly.

Two related but distinct things live here:
- `transition(current, target)` — the pure, exhaustive state machine over
  the full diagram. Used by the milestone test to assert every legal and
  illegal edge directly.
- `guard_event(current, event_type)` — what the interceptor actually calls
  before writing a real event, mapping v1's existing `EventType` vocabulary
  (unchanged here) onto `CallState`.

ALLOWED -> EXECUTING is a real edge in the diagram but has no event of its
own in v1's vocabulary (ARCHITECTURE.md §8: the interceptor never records
"started executing" — only the allow decision and the eventual outcome,
since the SDK executes the real call, not the interceptor). `guard_event`
treats this as an implicit, always-legal hop applied the instant a
CallCompleted/CallFailed event is validated against an ALLOWED span — an
explicit, tested pass-through (see the milestone test), not a silently
skipped guarantee.
"""

from __future__ import annotations

from enum import StrEnum

from .events import EventType


class CallState(StrEnum):
    CREATED = "created"
    ATTEMPTED = "attempted"
    BLOCKED = "blocked"
    PENDING_APPROVAL = "pending_approval"
    ALLOWED = "allowed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"


TERMINAL_STATES = frozenset(
    {CallState.BLOCKED, CallState.DENIED, CallState.COMPLETED, CallState.FAILED}
)


def is_terminal(state: CallState) -> bool:
    """Invariant #2: a TERMINAL state never transitions to any other state.
    Modeled as a derived property of these four states (each has zero
    outgoing edges in `_TRANSITIONS`), not a 9th stored state — recording a
    call as e.g. CallBlocked already *is* the terminal fact; a further
    "now terminal" event would be pure redundancy with no event type of
    its own. See ADR-017."""
    return state in TERMINAL_STATES


class IllegalStateTransition(Exception):
    def __init__(self, current: CallState, target: CallState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"illegal call-state transition: {current.value} -> {target.value}")


# Every edge drawn in UPGRADE_ARCHITECTURE.md §2's diagram, literally.
# Terminal states (see TERMINAL_STATES) map to an empty set — no outgoing
# edges is invariant #2's actual enforcement mechanism.
_TRANSITIONS: dict[CallState, frozenset[CallState]] = {
    CallState.CREATED: frozenset({CallState.ATTEMPTED}),
    CallState.ATTEMPTED: frozenset(
        {CallState.BLOCKED, CallState.PENDING_APPROVAL, CallState.ALLOWED}
    ),
    CallState.PENDING_APPROVAL: frozenset({CallState.ALLOWED, CallState.DENIED}),
    CallState.ALLOWED: frozenset({CallState.EXECUTING}),
    CallState.EXECUTING: frozenset({CallState.COMPLETED, CallState.FAILED}),
    CallState.BLOCKED: frozenset(),
    CallState.DENIED: frozenset(),
    CallState.COMPLETED: frozenset(),
    CallState.FAILED: frozenset(),
}


def transition(current: CallState, target: CallState) -> CallState:
    """Pure state machine (invariant #1 lives here, as data: BLOCKED has no
    edge to EXECUTING, full stop). Raises IllegalStateTransition for any
    pair not literally drawn in the §2 diagram, including self-transitions
    and anything out of a terminal state."""
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise IllegalStateTransition(current, target)
    return target


# -- Mapping v1's real EventType vocabulary onto CallState -----------------
# APPROVAL_GRANTED maps straight to ALLOWED (not a separate APPROVED state):
# v1's Phase 4 decision already treats ApprovalGranted as the "allowed"
# equivalent for a span resolved via the approval flow (main.py never emits
# a separate CallAllowed after it) — the state machine follows that existing
# vocabulary rather than inventing a state no event will ever produce.
_EVENT_TO_STATE: dict[EventType, CallState] = {
    EventType.CALL_ATTEMPTED: CallState.ATTEMPTED,
    EventType.CALL_BLOCKED: CallState.BLOCKED,
    EventType.CALL_PENDING_APPROVAL: CallState.PENDING_APPROVAL,
    EventType.CALL_ALLOWED: CallState.ALLOWED,
    EventType.APPROVAL_GRANTED: CallState.ALLOWED,
    EventType.APPROVAL_DENIED: CallState.DENIED,
    EventType.CALL_COMPLETED: CallState.COMPLETED,
    EventType.CALL_FAILED: CallState.FAILED,
}


def state_for_event(event_type: EventType) -> CallState:
    return _EVENT_TO_STATE[event_type]


def guard_event(current: CallState, event_type: EventType) -> CallState:
    """What the interceptor calls before every real event write. Applies
    the ALLOWED -> EXECUTING implicit hop described in the module
    docstring, then validates the actual target through `transition`."""
    target = state_for_event(event_type)
    if current is CallState.ALLOWED and target in (CallState.COMPLETED, CallState.FAILED):
        current = transition(current, CallState.EXECUTING)
    return transition(current, target)
