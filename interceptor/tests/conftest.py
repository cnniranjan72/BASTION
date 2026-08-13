from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from bastion_interceptor.db import db

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATE_SCRIPT = REPO_ROOT / "infra" / "db" / "migrate.py"
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://bastion:bastion@localhost:5442/bastion")


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    subprocess.run(
        [sys.executable, str(MIGRATE_SCRIPT)],
        check=True,
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": DATABASE_URL},
    )


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _db_pool(_migrated_database: None) -> AsyncIterator[None]:
    # main.py's `lifespan` normally does this on app startup; tests drive the
    # ASGI app directly via httpx.ASGITransport, which doesn't trigger
    # lifespan events, so the pool is opened/closed here instead.
    await db.connect()
    try:
        yield
    finally:
        await db.close()


@pytest_asyncio.fixture
async def test_agent() -> tuple[uuid.UUID, str]:
    """Inserts a fresh organization + agent directly via SQL and returns
    (agent_id, raw_api_key). Standing in for the not-yet-built POST /agents
    dashboard endpoint (Phase 5+, needs RBAC that doesn't exist yet) — this
    is a real DB row and a real SHA-256 hash, not a mock."""
    org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    raw_key = f"test_{uuid.uuid4().hex}"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name) VALUES ($1, $2)", org_id, "test-org"
        )
        await conn.execute(
            "INSERT INTO agents (id, org_id, name, api_key_hash) VALUES ($1, $2, $3, $4)",
            agent_id,
            org_id,
            "test-agent",
            key_hash,
        )
    finally:
        await conn.close()

    return agent_id, raw_key
