"""Mirrors the `events` table in DATA_MODEL.md and the event vocabulary in
ARCHITECTURE.md §2.4 — field-for-field. This is the append-only event core;
adding a new EventType is fine, renaming/removing one breaks every stored row.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class EventType(StrEnum):
    CALL_ATTEMPTED = "CallAttempted"
    POLICY_EVALUATED = "PolicyEvaluated"
    CALL_ALLOWED = "CallAllowed"
    CALL_BLOCKED = "CallBlocked"
    CALL_PENDING_APPROVAL = "CallPendingApproval"
    APPROVAL_GRANTED = "ApprovalGranted"
    APPROVAL_DENIED = "ApprovalDenied"
    CALL_COMPLETED = "CallCompleted"
    CALL_FAILED = "CallFailed"


class Event(BaseModel):
    event_id: UUID
    trace_id: UUID
    span_id: UUID
    parent_span_id: UUID | None
    agent_id: UUID
    event_type: EventType
    payload: dict[str, Any]
    sequence_number: int = Field(ge=0)
    created_at: datetime


class NewEvent(BaseModel):
    """Insert shape — the store assigns event_id, sequence_number, and created_at."""

    trace_id: UUID
    span_id: UUID
    parent_span_id: UUID | None
    agent_id: UUID
    event_type: EventType
    payload: dict[str, Any]


class CallAttemptedPayload(BaseModel):
    tool_name: str
    args: dict[str, Any]


class PolicyDecisionPayload(BaseModel):
    policy_id: UUID | None
    decision: Literal["allowed", "blocked", "pending_approval"]
    reason: str | None = None


class CallOutcomePayload(BaseModel):
    latency_ms: float = Field(ge=0)
    cost: float | None = Field(default=None, ge=0)
    result: Any | None = None
    error: str | None = None
