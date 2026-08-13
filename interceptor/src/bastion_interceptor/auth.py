"""Agent-to-BASTION (machine) auth — AUTH.md §1. SHA-256 is a deliberate
choice here: this is a lookup key, not a password, so no need for slow
hashing (argon2id is reserved for the human-user auth in Phase 5)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from fastapi import Header, HTTPException, Request

from .db import db


@dataclass(frozen=True)
class AuthenticatedAgent:
    id: UUID
    org_id: UUID
    name: str


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def authenticate_agent(
    request: Request, authorization: str | None = Header(default=None)
) -> AuthenticatedAgent:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "MISSING_API_KEY",
                    "message": "Authorization: Bearer <agent api key> header is required",
                    "request_id": request.state.request_id,
                }
            },
        )
    raw_key = authorization.removeprefix("Bearer ").strip()
    record = await db.get_agent_by_api_key_hash(hash_api_key(raw_key))
    if record is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "INVALID_API_KEY",
                    "message": "no agent matches this API key",
                    "request_id": request.state.request_id,
                }
            },
        )
    return AuthenticatedAgent(id=record["id"], org_id=record["org_id"], name=record["name"])
