"""Policy DSL, per ARCHITECTURE.md §2.3, and the `policies` table in DATA_MODEL.md."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PolicyMatch(BaseModel):
    tool: str  # exact tool name or "*" wildcard
    pattern: str | None = None  # regex tested against a tool-specific field (e.g. SQL text)
    database: str | None = None


class PolicyRule(BaseModel):
    """One rule in the policy DSL. Rules are evaluated top-to-bottom per
    (agent_id, tool_name); first match wins."""

    match: PolicyMatch
    action: Literal["allow", "block", "require_approval"]
    condition: str | None = None  # a small boolean expression over call args, e.g. "amount > 50"


PolicyDefinition = list[PolicyRule]


class Policy(BaseModel):
    """Policies are versioned, never edited in place."""

    id: UUID
    org_id: UUID
    name: str
    version: int = Field(gt=0)
    definition: PolicyDefinition
    active: bool
    created_at: datetime
