"""Idempotent seed data for the Phase 8 demo: a dedicated agent + policy
that blocks any `payments.transfer` over $100. Direct SQL against fixed
UUIDs, not `POST /agents`/`POST /policies` — standing in for the
not-yet-built agent-management endpoint, same convention as
`interceptor/tests/conftest.py`'s `test_agent`/`assign_policy_set_to_agent`
fixtures (documented there, and in docs/PROGRESS.md's open questions).

Reuses the org created by Phase 7's manual demo setup
(`11111111-1111-1111-1111-111111111111`, "demo-org") so this agent's traces
show up in the same dashboard session as everything else — creates the org
too if it doesn't exist, so this script is a complete standalone setup on a
fresh database.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid

import asyncpg
import redis.asyncio as redis

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://bastion:bastion@localhost:5442/bastion")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6389")

# ARCHITECTURE.md §2.3 / redis_bus.py's own hot-reload channel — publishing
# here after the direct SQL insert is what makes an *already-running*
# interceptor pick up this policy immediately, the same way it would if the
# policy had been created through POST /policies/{id}/activate instead.
POLICY_UPDATES_CHANNEL = "bastion:policy_updates"

ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_NAME = "demo-org"

AGENT_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
AGENT_NAME = "prompt-injection-demo"
AGENT_API_KEY = "prompt-injection-demo-key"  # plain, local-dev-only (cf. demo-agent-key)

POLICY_SET_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
POLICY_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")
POLICY_NAME = "prompt-injection-demo-policy"

POLICY_DEFINITION = [
    {"match": {"tool": "payments.transfer"}, "action": "block", "condition": "amount > 100"},
    {"match": {"tool": "*"}, "action": "allow"},
]


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def seed(database_url: str = DATABASE_URL) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
            ORG_ID,
            ORG_NAME,
        )
        await conn.execute(
            "INSERT INTO policy_sets (id, org_id, name) VALUES ($1, $2, $3) "
            "ON CONFLICT (id) DO NOTHING",
            POLICY_SET_ID,
            ORG_ID,
            POLICY_NAME,
        )
        await conn.execute(
            "INSERT INTO policies (id, org_id, policy_set_id, name, version, definition, active) "
            "VALUES ($1, $2, $3, $4, 1, $5::jsonb, true) "
            "ON CONFLICT (id) DO UPDATE SET definition = EXCLUDED.definition, active = true",
            POLICY_ID,
            ORG_ID,
            POLICY_SET_ID,
            POLICY_NAME,
            json.dumps(POLICY_DEFINITION),
        )
        await conn.execute(
            "INSERT INTO agents (id, org_id, name, api_key_hash, default_policy_set_id) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (id) DO UPDATE SET api_key_hash = EXCLUDED.api_key_hash, "
            "default_policy_set_id = EXCLUDED.default_policy_set_id",
            AGENT_ID,
            ORG_ID,
            AGENT_NAME,
            hash_api_key(AGENT_API_KEY),
            POLICY_SET_ID,
        )
    finally:
        await conn.close()

    # protocol=2 (RESP2): the RESP3 HELLO handshake this client defaults to
    # fails in this environment — same fix as redis_bus.py's connect().
    redis_client = redis.from_url(REDIS_URL, decode_responses=True, protocol=2)
    try:
        await redis_client.publish(
            POLICY_UPDATES_CHANNEL, json.dumps({"policy_set_id": str(POLICY_SET_ID)})
        )
    finally:
        await redis_client.aclose()

    print(f"Seeded org {ORG_ID} ({ORG_NAME})")
    print(f"Seeded agent {AGENT_ID} ({AGENT_NAME})")
    print(f"Agent API key (local dev only): {AGENT_API_KEY}")
    print(f"Active policy: {POLICY_NAME} - blocks payments.transfer where amount > 100")


if __name__ == "__main__":
    asyncio.run(seed())
