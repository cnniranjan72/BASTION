from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from bastion_interceptor import policy as policy_engine
from bastion_interceptor.db import db
from bastion_interceptor.redis_bus import redis_bus
from demo_agent.seed import POLICY_SET_ID, seed

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATE_SCRIPT = REPO_ROOT / "infra" / "db" / "migrate.py"
GENERATE_KEYS_SCRIPT = REPO_ROOT / "infra" / "keys" / "generate_dev_keys.py"
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://bastion:bastion@localhost:5442/bastion")


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    subprocess.run(
        [sys.executable, str(MIGRATE_SCRIPT)],
        check=True,
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": DATABASE_URL},
    )


@pytest.fixture(scope="session", autouse=True)
def _jwt_keys() -> None:
    subprocess.run([sys.executable, str(GENERATE_KEYS_SCRIPT)], check=True)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _db_pool(_migrated_database: None) -> AsyncIterator[None]:
    # Same reasoning as interceptor/tests/conftest.py: ASGITransport doesn't
    # trigger main.py's `lifespan`, so the pool is opened here instead.
    await db.connect()
    try:
        yield
    finally:
        await db.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _redis_bus_connected(_migrated_database: None) -> AsyncIterator[None]:
    await redis_bus.connect()
    try:
        yield
    finally:
        await redis_bus.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _seeded_demo_agent(_db_pool: None) -> None:
    """Runs the same idempotent seed the live demo uses, then loads the
    policy directly into this process's PolicyCache. `seed()`'s Redis
    publish only reaches an *already-running, already-subscribed*
    interceptor process — this ASGITransport-driven suite never starts one
    (no lifespan, no listener), so the cache is populated directly instead,
    the same stand-in reasoning as every other fixture here for
    endpoints/mechanisms that don't apply to an in-process test."""
    await seed(DATABASE_URL)
    record = await db.get_active_policy_for_set(POLICY_SET_ID)
    assert record is not None
    compiled = policy_engine.compile_policy_from_raw(
        record["id"], record["policy_set_id"], record["definition"]
    )
    policy_engine.policy_cache.put(compiled)
