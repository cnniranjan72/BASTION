"""GET/POST /policies, POST /policies/{id}/activate — API_SPEC.md's
"Human/dashboard API." Phase 2-4 had `org_id` as an explicit request field
(no auth existed yet — see docs/ARCHITECTURE.md §11); Phase 5 derives it
from the authenticated JWT session instead, so it's no longer part of the
request body at all."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from .policy import PolicyDefinition, PolicyLimits


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


class SimulatePolicyRequest(BaseModel):
    """U15 (v2 upgrade), Policy Studio's simulator — FRONTEND_V2.md: "paste
    a hypothetical tool call ... and see it walk through the actual
    evaluation chain ... using the real policy engine, not a UI-only
    approximation." `agent_id` resolves to that agent's real, currently
    assigned `default_policy_set_id`; nothing here is hypothetical except
    tool_name/args."""

    agent_id: UUID
    tool_name: str
    args: dict[str, object] = {}


class SimulatePolicyResponse(BaseModel):
    decision: Literal["allow", "block", "require_approval"]
    reason: str | None
    policy_id: UUID | None
    policy_set_id: UUID | None
    matched_rule_tool: str | None
    # Configured on the matched rule, shown for context — never actually
    # applied/checked against real Redis counters (that would consume the
    # agent's real rate-limit budget for a hypothetical call). See
    # docs/adr for the reasoning.
    configured_limits: PolicyLimits | None


class PolicyPropagationResponse(BaseModel):
    """U15 (v2 upgrade), Policy Studio's propagation-status panel.
    `known_interceptor_instances` is honestly 1 in this deployment — no
    multi-replica registry exists (see docs/adr) — this reports real state
    for the single instance handling the request, not a fabricated fleet
    count."""

    policy_set_id: UUID
    active_version: int
    active_policy_id: UUID
    this_instance_cached_version: int | None
    propagated: bool
    known_interceptor_instances: int
