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
    # U2 (v2 upgrade), UPGRADE_ARCHITECTURE.md §3. Optional at the wire
    # level for backward compatibility with any raw HTTP caller — the
    # Python SDK always generates and attaches one per logical call
    # (bastion/client.py), so real callers get the guarantee by default. A
    # call made without one gets v1's original behavior: always a fresh
    # span, no dedup, no idempotency protection — a deliberate, documented
    # degrade, not a silent gap.
    idempotency_key: str | None = None


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
