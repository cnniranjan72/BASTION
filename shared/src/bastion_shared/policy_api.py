"""GET/POST /policies, POST /policies/{id}/activate — API_SPEC.md's
"Human/dashboard API." Phase 2-4 had `org_id` as an explicit request field
(no auth existed yet — see docs/ARCHITECTURE.md §11); Phase 5 derives it
from the authenticated JWT session instead, so it's no longer part of the
request body at all."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from .policy import PolicyDefinition


class CreatePolicyRequest(BaseModel):
    name: str
    definition: PolicyDefinition
    # U4 (v2 upgrade), optimistic concurrency: the version the caller last
    # saw as current for this policy's policy_set — omitted preserves v1's
    # original blind-append behavior (no conflict detection); supplied, the
    # server 409s instead of creating a version past a concurrent editor's
    # already-committed one. See docs/adr/ADR-016.
    based_on_version: int | None = None


class PolicyResponse(BaseModel):
    id: UUID
    org_id: UUID
    policy_set_id: UUID
    name: str
    version: int
    definition: PolicyDefinition
    active: bool
    created_at: datetime
