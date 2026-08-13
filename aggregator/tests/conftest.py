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
from bastion_aggregator.db import db
from bastion_aggregator.main import _handle_notification, listener
from bastion_interceptor.db import db as interceptor_db
from bastion_interceptor.redis_bus import redis_bus as interceptor_redis_bus

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
    # lifespan events (same reasoning as interceptor/tests/conftest.py).
    await db.connect()
    try:
        yield
    finally:
        await db.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _event_listener(_db_pool: None) -> AsyncIterator[None]:
    await listener.start(_handle_notification)
    try:
        yield
    finally:
        await listener.stop()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _interceptor_connected(_migrated_database: None) -> AsyncIterator[None]:
    """Cross-service test setup: generating real trace data to replay needs
    a fully functional interceptor app (POST /intercept), which has its own
    db/redis_bus singletons, separate from the aggregator's."""
    await interceptor_db.connect()
    await interceptor_redis_bus.connect()
    try:
        yield
    finally:
        await interceptor_redis_bus.close()
        await interceptor_db.close()


@pytest_asyncio.fixture
async def test_org() -> uuid.UUID:
    org_id = uuid.uuid4()
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name) VALUES ($1, $2)", org_id, f"test-org-{org_id}"
        )
    finally:
        await conn.close()
    return org_id


@pytest_asyncio.fixture
async def test_agent(test_org: uuid.UUID) -> tuple[uuid.UUID, str]:
    """Same stand-in reasoning as interceptor/tests/conftest.py — no
    POST /agents endpoint until Phase 5."""
    agent_id = uuid.uuid4()
    raw_key = f"test_{uuid.uuid4().hex}"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "INSERT INTO agents (id, org_id, name, api_key_hash) VALUES ($1, $2, $3, $4)",
            agent_id,
            test_org,
            "test-agent",
            key_hash,
        )
    finally:
        await conn.close()

    return agent_id, raw_key
