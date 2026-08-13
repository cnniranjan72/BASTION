"""Human user auth, verify-only — AUTH.md §2. The aggregator never issues
tokens (no login/refresh here, that's the interceptor's job); it only needs
the public key to verify a JWT's signature, exactly the point of asymmetric
signing (AUTH.md: verifiable "without calling the auth service"). No RBAC
here either — every /traces endpoint is read-only, any authenticated role
can view (unlike interceptor's policy/approval writes, which do check role).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from bastion_shared import AccessTokenClaims, InvalidAccessToken, UserRole, decode_access_token
from fastapi import Header, HTTPException, Request

from .config import config


@lru_cache(maxsize=1)
def _public_key_pem() -> str:
    with open(config.jwt_public_key_path, encoding="utf-8") as f:
        return f.read()


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    org_id: UUID
    role: UserRole


def decode_bearer_token(token: str) -> AuthenticatedUser:
    """Shared by the HTTP dependency below and WS /live/{agent_id} (main.py)
    — a WebSocket handshake can't carry a custom Authorization header from
    browser JS, so that endpoint takes the token as a query param instead
    and calls this directly rather than going through the Header-based
    FastAPI dependency."""
    claims: AccessTokenClaims = decode_access_token(token, _public_key_pem())
    return AuthenticatedUser(id=claims.user_id, org_id=claims.org_id, role=claims.role)


async def authenticate_user(
    request: Request, authorization: str | None = Header(default=None)
) -> AuthenticatedUser:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "MISSING_ACCESS_TOKEN",
                    "message": "Authorization: Bearer <access token> header is required",
                    "request_id": request.state.request_id,
                }
            },
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return decode_bearer_token(token)
    except InvalidAccessToken as exc:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "INVALID_ACCESS_TOKEN",
                    "message": str(exc),
                    "request_id": request.state.request_id,
                }
            },
        ) from exc
