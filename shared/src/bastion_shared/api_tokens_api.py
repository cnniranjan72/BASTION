"""GET/POST /api-tokens, DELETE /api-tokens/{id} — personal API tokens.

A third auth domain alongside AUTH.md's two (human JWT sessions, machine
agent keys): a long-lived credential a *human* can hand to a script, CI job,
or external integration to call the management API without an interactive
login/refresh cycle. Reuses the exact org/role scoping a JWT session gets —
`authenticate_user` accepts either — so an API token is not a separate,
weaker auth path, just a different way to present the same identity.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CreateApiTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="A label, e.g. 'CI pipeline'.")


class ApiTokenResponse(BaseModel):
    id: UUID
    name: str
    token_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class CreateApiTokenResponse(ApiTokenResponse):
    token: str = Field(description="Shown once — only the hash is ever stored after this response.")
