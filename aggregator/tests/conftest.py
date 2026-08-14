from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio
from bastion_aggregator.db import db
from bastion_aggregator.main import _handle_notification, kafka_consumer
from bastion_aggregator.redis_bus import redis_bus as aggregator_redis_bus
from bastion_interceptor.db import db as interceptor_db
from bastion_interceptor.human_auth import hash_password
from bastion_interceptor.main import app as interceptor_app
from bastion_interceptor.outbox_publisher import OutboxPublisher
from bastion_interceptor.redis_bus import redis_bus as interceptor_redis_bus

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
    # main.py's `lifespan` normally does this on app startup; tests drive the
    # ASGI app directly via httpx.ASGITransport, which doesn't trigger
    # lifespan events (same reasoning as interceptor/tests/conftest.py).
    await db.connect()
    try:
        yield
    finally:
        await db.close()


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


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _aggregator_redis_connected() -> AsyncIterator[None]:
    """U11 (v2 upgrade): ws.py's ConnectionManager publishes/subscribes
    through this — main.py's lifespan normally connects it, but tests
    drive the ASGI app via httpx.ASGITransport, which doesn't trigger
    lifespan events (same reasoning as _db_pool above)."""
    await aggregator_redis_bus.connect()
    try:
        yield
    finally:
        await aggregator_redis_bus.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _event_pipeline(
    _db_pool: None, _interceptor_connected: None, _aggregator_redis_connected: None
) -> AsyncIterator[None]:
    """U3 (v2 upgrade): the real production pipeline, not a test shortcut —
    interceptor writes events + outbox rows, the outbox publisher ships
    them to Kafka, the aggregator's Kafka consumer calls
    _handle_notification exactly as it does in production. Replaces v1's
    directly-wired Postgres LISTEN/NOTIFY listener; every aggregator test
    that depends on live-tracking/WS behavior now exercises the actual v2
    data path end to end, per CLAUDE.md's no-mock-data rule."""
    publisher = OutboxPublisher()
    publisher_task = asyncio.create_task(publisher.run_forever())
    await kafka_consumer.start(_handle_notification)
    try:
        yield
    finally:
        await kafka_consumer.stop()
        publisher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await publisher_task
        await publisher.stop()


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


@pytest.fixture
def assign_policy_set_to_agent():
    """Same stand-in reasoning as the fixture of the same name in
    interceptor/tests/conftest.py — no agent-management endpoint yet."""

    async def _assign(agent_id: uuid.UUID, policy_set_id: uuid.UUID) -> None:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute(
                "UPDATE agents SET default_policy_set_id = $1 WHERE id = $2",
                policy_set_id,
                agent_id,
            )
        finally:
            await conn.close()

    return _assign


DEFAULT_TEST_PASSWORD = "correct horse battery staple"


@pytest_asyncio.fixture
def make_user(test_org: uuid.UUID):
    """Same reasoning as interceptor/tests/conftest.py's fixture of the same
    name — duplicated rather than shared across packages' test suites."""

    async def _make(role: str = "owner", org_id: uuid.UUID | None = None) -> dict[str, object]:
        user_id = uuid.uuid4()
        email = f"{user_id}@example.com"
        target_org = org_id if org_id is not None else test_org
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute(
                "INSERT INTO users (id, org_id, email, password_hash, role) "
                "VALUES ($1, $2, $3, $4, $5)",
                user_id,
                target_org,
                email,
                hash_password(DEFAULT_TEST_PASSWORD),
                role,
            )
        finally:
            await conn.close()
        return {
            "id": user_id,
            "org_id": target_org,
            "email": email,
            "password": DEFAULT_TEST_PASSWORD,
            "role": role,
        }

    return _make


@pytest_asyncio.fixture
def login_as():
    """Logs in via the *interceptor's* app (the only thing that issues
    tokens) — the aggregator only ever verifies them."""

    async def _login(user: dict[str, object]) -> dict[str, str]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=interceptor_app), base_url="http://interceptor.test"
        ) as http:
            response = await http.post(
                "/auth/login", json={"email": user["email"], "password": user["password"]}
            )
        response.raise_for_status()
        return response.json()

    return _login
