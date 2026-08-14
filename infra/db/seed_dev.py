"""Idempotent dev-only seed: the base demo org + one owner-role human user,
so a fresh checkout has something to log into the dashboard with. Separate
from `demo-agent/demo_agent/seed.py`, which seeds the Phase 8 scenario's
specific agent + policy — this script only needs to run once per database,
that one is its own self-contained demo setup layered on top of this.

Standing in for the not-yet-built signup/`POST /users` endpoint (same
reasoning as `interceptor/tests/conftest.py`'s `make_user` fixture — direct
SQL, not a mock).

Usage: uv run python infra/db/seed_dev.py
"""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
from bastion_interceptor.human_auth import hash_password

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://bastion:bastion@localhost:5442/bastion")

ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_NAME = "demo-org"

USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
USER_EMAIL = "demo@bastion.dev"
USER_PASSWORD = "demo-password-123"  # plain, local-dev-only


async def seed(database_url: str = DATABASE_URL) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
            ORG_ID,
            ORG_NAME,
        )
        await conn.execute(
            "INSERT INTO users (id, org_id, email, password_hash, role) "
            "VALUES ($1, $2, $3, $4, 'owner') "
            "ON CONFLICT (id) DO UPDATE SET password_hash = EXCLUDED.password_hash",
            USER_ID,
            ORG_ID,
            USER_EMAIL,
            hash_password(USER_PASSWORD),
        )
    finally:
        await conn.close()

    print(f"Seeded org {ORG_ID} ({ORG_NAME})")
    print(f"Seeded user {USER_EMAIL} (role: owner)")
    print(f"Login: {USER_EMAIL} / {USER_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(seed())
