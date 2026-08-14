"""POST /auth/login, /auth/signup, /auth/refresh, /auth/logout — API_SPEC.md, AUTH.md §2."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from .jwt_auth import UserRole


class LoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    """Creates a brand-new organization and its first user (role: owner),
    then logs them in immediately — self-serve signup, not an invite flow.
    No password policy was specced anywhere (AUTH.md, API_SPEC.md); an
    8-character minimum is a reasonable default, not a documented
    requirement copied from elsewhere."""

    org_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8)


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    role: UserRole


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str
