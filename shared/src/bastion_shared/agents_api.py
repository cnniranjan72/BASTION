"""POST/GET /agents, PATCH /agents/{id} — closes the gap `docs/decisions.md`
and `docs/PROGRESS.md` tracked since Phase 2: agents were only ever
creatable via direct SQL (tests, seed scripts), with no real endpoint and
no UI. AgentResponse never carries the API key — only CreateAgentResponse
does, once, at creation time; the DB only ever stores its hash (`auth.py`),
so there is no way to retrieve a lost key later, by design (same as e.g.
GitHub personal access tokens)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    policy_set_id: UUID | None = None


class AgentResponse(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    policy_set_id: UUID | None
    created_at: datetime


class CreateAgentResponse(AgentResponse):
    api_key: str


class UpdateAgentRequest(BaseModel):
    policy_set_id: UUID | None
