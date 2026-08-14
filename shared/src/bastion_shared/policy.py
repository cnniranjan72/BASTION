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


class PolicyLimits(BaseModel):
    """U6 (v2 upgrade), UPGRADE_ARCHITECTURE.md §8 — stateful governance,
    checked only once a rule's match/condition have already decided a call
    would otherwise be `allow` (ADR-015). Unlike `condition`, these need
    real cross-call state (Redis counters), not the stateless per-call
    safe-eval `condition` field.

    `calls_per_minute` does double duty for both "per agent" and "per tool"
    from §8's list: a rule matching `tool: "*"` scopes it per-agent, a rule
    naming a specific tool scopes it per-(agent, tool) — same field, same
    enforcement code, the rule's own `match.tool` decides which dimension
    it is. `org_spend_per_day` and `agent_llm_budget_per_hour` are both
    real, working spend accumulators, differing only in window/key.
    Deliberately not implemented (see ADR-015, scope explicitly documented
    rather than silently dropped): a distinct tool-call-count budget
    (redundant with `calls_per_minute`) and a runtime/duration budget (not
    knowable until CallCompleted/CallFailed, after the decision point these
    limits gate)."""

    calls_per_minute: int | None = None
    max_transaction_amount: float | None = None
    org_spend_per_day: float | None = None
    agent_llm_budget_per_hour: float | None = None


class PolicyRule(BaseModel):
    """One rule in the policy DSL. Rules are evaluated top-to-bottom per
    (agent_id, tool_name); first match wins."""

    match: PolicyMatch
    action: Literal["allow", "block", "require_approval"]
    condition: str | None = None  # a small boolean expression over call args, e.g. "amount > 50"
    limits: PolicyLimits | None = None


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
