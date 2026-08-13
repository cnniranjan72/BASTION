"""POST /intercept request/response contract — API_SPEC.md §Machine API."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class InterceptRequest(BaseModel):
    trace_id: UUID
    parent_span_id: UUID | None
    tool_name: str
    args: dict[str, Any]
    agent_id: UUID


class InterceptAllowedResponse(BaseModel):
    span_id: UUID
    decision: Literal["allowed"] = "allowed"
    policy_id: UUID | None
    result: Any | None = None


class InterceptBlockedResponse(BaseModel):
    span_id: UUID
    decision: Literal["blocked"] = "blocked"
    policy_id: UUID | None
    reason: str


class InterceptPendingResponse(BaseModel):
    span_id: UUID
    decision: Literal["pending_approval"] = "pending_approval"
    approval_request_id: UUID
    poll_url: str


InterceptResponse = Annotated[
    InterceptAllowedResponse | InterceptBlockedResponse | InterceptPendingResponse,
    Field(discriminator="decision"),
]
