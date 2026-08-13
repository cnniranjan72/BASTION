"""POST /auth/login, /auth/refresh, /auth/logout — API_SPEC.md, AUTH.md §2."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .jwt_auth import UserRole


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    role: UserRole


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str
