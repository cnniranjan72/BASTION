"""U1 milestone test (UPGRADE_BUILD_PLAN.md): every illegal transition in
the §2 diagram is rejected, every legal path succeeds end to end, and every
terminal state rejects all further transitions — exhaustively, not by
sampling a few cases.
"""

from __future__ import annotations

import itertools

import pytest
from bastion_shared.call_state import (
    CallState,
    IllegalStateTransition,
    guard_event,
    is_terminal,
    state_for_event,
    transition,
)
from bastion_shared.events import EventType

# The literal edge set from UPGRADE_ARCHITECTURE.md §2 — the source of
# truth this test checks the module against, kept independent of
# call_state.py's own internal table so a bug in one isn't masked by the
# other agreeing with itself.
LEGAL_EDGES: set[tuple[CallState, CallState]] = {
    (CallState.CREATED, CallState.ATTEMPTED),
    (CallState.ATTEMPTED, CallState.BLOCKED),
    (CallState.ATTEMPTED, CallState.PENDING_APPROVAL),
    (CallState.ATTEMPTED, CallState.ALLOWED),
    (CallState.PENDING_APPROVAL, CallState.ALLOWED),
    (CallState.PENDING_APPROVAL, CallState.DENIED),
    (CallState.ALLOWED, CallState.EXECUTING),
    (CallState.EXECUTING, CallState.COMPLETED),
    (CallState.EXECUTING, CallState.FAILED),
}

ALL_STATES = list(CallState)


@pytest.mark.parametrize(
    "current,target", sorted(LEGAL_EDGES, key=lambda p: (p[0].value, p[1].value))
)
def test_every_legal_edge_succeeds(current: CallState, target: CallState) -> None:
    assert transition(current, target) == target


@pytest.mark.parametrize(
    "current,target",
    [(c, t) for c, t in itertools.product(ALL_STATES, ALL_STATES) if (c, t) not in LEGAL_EDGES],
)
def test_every_illegal_pair_is_rejected(current: CallState, target: CallState) -> None:
    """Exhaustive over the full state x state matrix minus the legal edge
    set above — includes every self-transition, every reversed edge, and
    every skip-ahead (e.g. ATTEMPTED -> EXECUTING directly)."""
    with pytest.raises(IllegalStateTransition):
        transition(current, target)


@pytest.mark.parametrize(
    "state", [CallState.BLOCKED, CallState.DENIED, CallState.COMPLETED, CallState.FAILED]
)
def test_terminal_states_reject_every_further_transition(state: CallState) -> None:
    assert is_terminal(state)
    for target in ALL_STATES:
        with pytest.raises(IllegalStateTransition):
            transition(state, target)


def test_non_terminal_states_are_not_terminal() -> None:
    non_terminal = (
        CallState.CREATED,
        CallState.ATTEMPTED,
        CallState.PENDING_APPROVAL,
        CallState.ALLOWED,
        CallState.EXECUTING,
    )
    for state in non_terminal:
        assert not is_terminal(state)


def test_blocked_can_never_reach_executing_via_any_path() -> None:
    """Invariant #1, stated directly: BLOCKED has zero outgoing edges, so
    no path — not just the direct one — can ever reach EXECUTING from it."""
    assert _TRANSITIONS_EMPTY(CallState.BLOCKED)


def _TRANSITIONS_EMPTY(state: CallState) -> bool:
    for target in ALL_STATES:
        try:
            transition(state, target)
        except IllegalStateTransition:
            continue
        return False
    return True


# -- guard_event: the real event-vocabulary mapping the interceptor uses --


def test_full_allowed_path_via_guard_event() -> None:
    state = CallState.CREATED
    state = guard_event(state, EventType.CALL_ATTEMPTED)
    assert state == CallState.ATTEMPTED
    state = guard_event(state, EventType.CALL_ALLOWED)
    assert state == CallState.ALLOWED
    # No event marks entering EXECUTING in v1's vocabulary — guard_event
    # applies that hop implicitly and explicitly (see module docstring),
    # then validates CallCompleted lands on COMPLETED.
    state = guard_event(state, EventType.CALL_COMPLETED)
    assert state == CallState.COMPLETED
    assert is_terminal(state)


def test_full_blocked_path_via_guard_event() -> None:
    state = guard_event(CallState.CREATED, EventType.CALL_ATTEMPTED)
    state = guard_event(state, EventType.CALL_BLOCKED)
    assert state == CallState.BLOCKED
    assert is_terminal(state)


def test_full_approval_granted_path_via_guard_event() -> None:
    state = guard_event(CallState.CREATED, EventType.CALL_ATTEMPTED)
    state = guard_event(state, EventType.CALL_PENDING_APPROVAL)
    assert state == CallState.PENDING_APPROVAL
    state = guard_event(state, EventType.APPROVAL_GRANTED)
    assert state == CallState.ALLOWED
    state = guard_event(state, EventType.CALL_FAILED)
    assert state == CallState.FAILED
    assert is_terminal(state)


def test_full_approval_denied_path_via_guard_event() -> None:
    state = guard_event(CallState.CREATED, EventType.CALL_ATTEMPTED)
    state = guard_event(state, EventType.CALL_PENDING_APPROVAL)
    state = guard_event(state, EventType.APPROVAL_DENIED)
    assert state == CallState.DENIED
    assert is_terminal(state)


def test_completing_a_blocked_span_is_rejected() -> None:
    """The exact real-world bug this replaces: v1's /spans/{id}/complete
    manually checked `event_type not in (CALL_ALLOWED, APPROVAL_GRANTED)`.
    Now the state machine itself rejects it."""
    with pytest.raises(IllegalStateTransition):
        guard_event(CallState.BLOCKED, EventType.CALL_COMPLETED)


def test_completing_a_pending_approval_span_is_rejected() -> None:
    with pytest.raises(IllegalStateTransition):
        guard_event(CallState.PENDING_APPROVAL, EventType.CALL_COMPLETED)


def test_double_completion_is_rejected() -> None:
    with pytest.raises(IllegalStateTransition):
        guard_event(CallState.COMPLETED, EventType.CALL_FAILED)


def test_state_for_event_covers_every_real_event_type_used_in_call_lifecycle() -> None:
    for event_type in (
        EventType.CALL_ATTEMPTED,
        EventType.CALL_BLOCKED,
        EventType.CALL_PENDING_APPROVAL,
        EventType.CALL_ALLOWED,
        EventType.APPROVAL_GRANTED,
        EventType.APPROVAL_DENIED,
        EventType.CALL_COMPLETED,
        EventType.CALL_FAILED,
    ):
        assert isinstance(state_for_event(event_type), CallState)
