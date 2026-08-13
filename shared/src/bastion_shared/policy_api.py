"""GET/POST /policies, POST /policies/{id}/activate — API_SPEC.md's
"Human/dashboard API." `org_id` is an explicit request field for now, not
derived from a session — see docs/ARCHITECTURE.md §11: these endpoints are
unauthenticated until Phase 5 retrofits JWT + RBAC, by BUILD_PLAN.md's own
phase order, not by oversight."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from .policy import PolicyDefinition


class CreatePolicyRequest(BaseModel):
    org_id: UUID
    name: str
    definition: PolicyDefinition


class PolicyResponse(BaseModel):
    id: UUID
    org_id: UUID
    policy_set_id: UUID
    name: str
    version: int
    definition: PolicyDefinition
    active: bool
    created_at: datetime
