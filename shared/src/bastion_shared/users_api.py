"""GET/POST /users, PATCH /users/{id}/role — team/RBAC management.

Closes a real gap: `docs/AUTH.md`'s four roles (owner/admin/approver/viewer)
were enforced everywhere since Phase 5, but the only way a second user ever
joined an org was direct SQL — no invite flow, no way to see who else is in
your org, no way to change anyone's role after signup. This is a
*provisioning* flow (an owner/admin directly creates a teammate's account
with a role), not an email-invite flow — no email-sending infrastructure
exists anywhere in this project, and pretending otherwise would be exactly
the kind of silent mock CLAUDE.md rule #3 prohibits. The created user's
password is generated and returned once, the same one-time-reveal pattern
`CreateAgentResponse.api_key` already established, so the admin can hand it
to the teammate out of band.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from .jwt_auth import UserRole


class CreateUserRequest(BaseModel):
    email: EmailStr
    role: UserRole = "viewer"


class UserResponse(BaseModel):
    id: UUID
    org_id: UUID
    email: str
    role: UserRole
    created_at: datetime


class CreateUserResponse(UserResponse):
    temporary_password: str


class UpdateUserRoleRequest(BaseModel):
    role: UserRole = Field(description="Cannot demote the org's last remaining owner.")
