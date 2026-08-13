"""JWT access token encode/decode — AUTH.md §2: "signed with an asymmetric
key so the interceptor/aggregator services can verify it without calling
the auth service." Both services import this directly (their own copy of
`decode_access_token`, using only the public key); only the interceptor
(where login/refresh live) ever calls `encode_access_token`, since only it
holds the private key.

Access tokens are stateless — short TTL (15 min) is the only revocation
mechanism (AUTH.md: "you don't revoke them individually; you rely on their
short TTL"). That's a deliberate tradeoff, not an oversight: instant
revocation would need a denylist cache (Redis, short TTL) checked on every
request, paying a lookup cost per request to shave at most 15 minutes off
a compromised token's remaining life. Not implemented in v1 — noted here so
the tradeoff is explicit, not silently absent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

import jwt

ALGORITHM = "EdDSA"
ACCESS_TOKEN_TTL_SECONDS = 15 * 60

UserRole = Literal["owner", "admin", "approver", "viewer"]


class InvalidAccessToken(Exception):
    pass


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: UUID
    org_id: UUID
    role: UserRole


def encode_access_token(claims: AccessTokenClaims, private_key_pem: str) -> str:
    now = int(time.time())
    payload = {
        "sub": str(claims.user_id),
        "org_id": str(claims.org_id),
        "role": claims.role,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, private_key_pem, algorithm=ALGORITHM)


def decode_access_token(token: str, public_key_pem: str) -> AccessTokenClaims:
    try:
        payload = jwt.decode(token, public_key_pem, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidAccessToken(str(exc)) from exc
    try:
        return AccessTokenClaims(
            user_id=UUID(payload["sub"]),
            org_id=UUID(payload["org_id"]),
            role=payload["role"],
        )
    except (KeyError, ValueError) as exc:
        raise InvalidAccessToken("malformed claims") from exc


def load_key_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")
