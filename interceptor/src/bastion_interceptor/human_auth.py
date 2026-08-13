"""Human user (dashboard/approvals) auth — AUTH.md §2. Deliberately separate
from agent.py's machine auth: AUTH.md is explicit that these are "two
separate auth domains, don't conflate them." argon2id here (slow, tuned for
password hashing) vs. SHA-256 for agent API keys (fast, a lookup key isn't
a password) is the concrete expression of that split.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from bastion_shared import AccessTokenClaims, InvalidAccessToken, UserRole, decode_access_token
from fastapi import Depends, Header, HTTPException, Request

from .config import config

_hasher = PasswordHasher()


def hash_password(raw_password: str) -> str:
    return _hasher.hash(raw_password)


def verify_password(raw_password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, raw_password)
    except VerifyMismatchError:
        return False


@lru_cache(maxsize=1)
def _public_key_pem() -> str:
    with open(config.jwt_public_key_path, encoding="utf-8") as f:
        return f.read()


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    org_id: UUID
    role: UserRole


def _unauthorized(request: Request, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "error": {"code": code, "message": message, "request_id": request.state.request_id}
        },
    )


async def authenticate_user(
    request: Request, authorization: str | None = Header(default=None)
) -> AuthenticatedUser:
    if authorization is None or not authorization.startswith("Bearer "):
        raise _unauthorized(
            request,
            "MISSING_ACCESS_TOKEN",
            "Authorization: Bearer <access token> header is required",
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims: AccessTokenClaims = decode_access_token(token, _public_key_pem())
    except InvalidAccessToken as exc:
        raise _unauthorized(request, "INVALID_ACCESS_TOKEN", str(exc)) from exc
    return AuthenticatedUser(id=claims.user_id, org_id=claims.org_id, role=claims.role)


_authenticated_user_dep = Depends(authenticate_user)


def require_role(*roles: UserRole) -> Callable[..., Coroutine[Any, Any, AuthenticatedUser]]:
    """FastAPI dependency factory — never trust the frontend to hide a
    button as the only control (AUTH.md §2 RBAC section)."""

    async def _dependency(
        request: Request, user: AuthenticatedUser = _authenticated_user_dep
    ) -> AuthenticatedUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": "FORBIDDEN",
                        "message": f"role '{user.role}' cannot perform this action",
                        "request_id": request.state.request_id,
                    }
                },
            )
        return user

    return _dependency
