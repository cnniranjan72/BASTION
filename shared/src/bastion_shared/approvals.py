"""GET /approvals/{id}, GET /approvals?status=, POST /approvals/{id}/approve|deny
— API_SPEC.md. GET /approvals/{id} is the SDK's long-poll target (not
/intercept itself — see docs/ARCHITECTURE.md's approval-flow section for
why). The approve/deny endpoints are dashboard endpoints, unauthenticated
until Phase 5 like /policies (docs/ARCHITECTURE.md §11); `resolved_by` is
always null until then (no `users` table yet).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

ApprovalStatus = Literal["pending", "approved", "denied", "timed_out"]


class ApprovalRequestResponse(BaseModel):
    id: UUID
    trace_id: UUID
    span_id: UUID
    status: ApprovalStatus
    requested_at: datetime
    resolved_by: UUID | None
    resolved_at: datetime | None
