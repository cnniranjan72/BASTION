from datetime import UTC, datetime
from uuid import UUID

import pytest
from bastion_shared import Event, EventType, NewEvent
from pydantic import ValidationError

VALID = {
    "event_id": "11111111-1111-1111-1111-111111111111",
    "trace_id": "22222222-2222-2222-2222-222222222222",
    "span_id": "33333333-3333-3333-3333-333333333333",
    "parent_span_id": None,
    "agent_id": "44444444-4444-4444-4444-444444444444",
    "event_type": "CallAttempted",
    "payload": {"tool_name": "db.query", "args": {}},
    "sequence_number": 0,
    "created_at": datetime.now(UTC),
}


def test_accepts_a_well_formed_event():
    event = Event.model_validate(VALID)
    assert event.event_id == UUID(VALID["event_id"])
    assert event.event_type is EventType.CALL_ATTEMPTED


def test_rejects_an_unknown_event_type():
    with pytest.raises(ValidationError):
        Event.model_validate({**VALID, "event_type": "CallImagined"})


def test_covers_every_event_type_from_architecture_md():
    assert [e.value for e in EventType] == [
        "CallAttempted",
        "PolicyEvaluated",
        "CallAllowed",
        "CallBlocked",
        "CallPendingApproval",
        "ApprovalGranted",
        "ApprovalDenied",
        "CallCompleted",
        "CallFailed",
    ]


def test_new_event_schema_omits_server_assigned_fields():
    omitted = ("event_id", "sequence_number", "created_at")
    rest = {k: v for k, v in VALID.items() if k not in omitted}
    NewEvent.model_validate(rest)
