"""U2 milestone test (UPGRADE_BUILD_PLAN.md): fire the same call with the
same idempotency key 5 times concurrently; assert the downstream side
effect happened exactly once and all 5 callers got the same result.

"Downstream side effect happened exactly once" is verified the way it
actually has to be at the interceptor layer (the interceptor never
executes the downstream call itself — the SDK does, per ARCHITECTURE.md
§8): exactly one CallAttempted event is ever written for the shared
idempotency key, proving only one of the five concurrent evaluations was
allowed to proceed — the SDK on the other end would only ever see one
distinct span_id/decision to execute against.
"""

from __future__ import annotations

import asyncio
import uuid
from uuid import UUID

import httpx
from bastion_interceptor.db import db
from bastion_interceptor.main import app
from bastion_shared.events import EventType


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://interceptor.test"
    )


async def test_five_concurrent_identical_calls_produce_exactly_one_side_effect(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, raw_key = test_agent
    trace_id = uuid.uuid4()
    idempotency_key = str(uuid.uuid4())
    headers = {"Authorization": f"Bearer {raw_key}"}
    body = {
        "trace_id": str(trace_id),
        "parent_span_id": None,
        "tool_name": "payments.refund",
        "args": {"amount": 25},
        "agent_id": str(agent_id),
        "idempotency_key": idempotency_key,
    }

    async def fire_one() -> dict[str, object]:
        async with _http_client() as http:
            response = await http.post("/intercept", json=body, headers=headers)
        assert response.status_code == 200, response.text
        result: dict[str, object] = response.json()
        return result

    responses = await asyncio.gather(*(fire_one() for _ in range(5)))

    span_ids = {r["span_id"] for r in responses}
    assert len(span_ids) == 1, f"expected one shared span_id, got {span_ids}"
    decisions = {r["decision"] for r in responses}
    assert decisions == {"allowed"}, responses

    events = await db.get_events_for_trace(trace_id)
    attempted = [e for e in events if e["event_type"] == EventType.CALL_ATTEMPTED.value]
    assert len(attempted) == 1, (
        f"expected exactly one CallAttempted despite 5 concurrent identical requests, "
        f"got {len(attempted)}"
    )
    allowed = [e for e in events if e["event_type"] == EventType.CALL_ALLOWED.value]
    assert len(allowed) == 1


async def test_sequential_retry_with_same_key_returns_identical_response(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, raw_key = test_agent
    trace_id = uuid.uuid4()
    idempotency_key = str(uuid.uuid4())
    headers = {"Authorization": f"Bearer {raw_key}"}
    body = {
        "trace_id": str(trace_id),
        "parent_span_id": None,
        "tool_name": "payments.refund",
        "args": {"amount": 10},
        "agent_id": str(agent_id),
        "idempotency_key": idempotency_key,
    }

    async with _http_client() as http:
        first = await http.post("/intercept", json=body, headers=headers)
        assert first.status_code == 200
        second = await http.post("/intercept", json=body, headers=headers)
        assert second.status_code == 200

    assert first.json() == second.json()

    events = await db.get_events_for_trace(trace_id)
    attempted = [e for e in events if e["event_type"] == EventType.CALL_ATTEMPTED.value]
    assert len(attempted) == 1


async def test_different_idempotency_keys_are_independent(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, raw_key = test_agent
    trace_id = uuid.uuid4()
    headers = {"Authorization": f"Bearer {raw_key}"}

    async def call(key: str) -> dict[str, object]:
        async with _http_client() as http:
            response = await http.post(
                "/intercept",
                json={
                    "trace_id": str(trace_id),
                    "parent_span_id": None,
                    "tool_name": "payments.refund",
                    "args": {"amount": 5},
                    "agent_id": str(agent_id),
                    "idempotency_key": key,
                },
                headers=headers,
            )
        assert response.status_code == 200
        result: dict[str, object] = response.json()
        return result

    first = await call(str(uuid.uuid4()))
    second = await call(str(uuid.uuid4()))
    assert first["span_id"] != second["span_id"]


async def test_no_idempotency_key_behaves_like_v1_always_fresh_span(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, raw_key = test_agent
    trace_id = uuid.uuid4()
    headers = {"Authorization": f"Bearer {raw_key}"}
    body = {
        "trace_id": str(trace_id),
        "parent_span_id": None,
        "tool_name": "payments.refund",
        "args": {"amount": 5},
        "agent_id": str(agent_id),
    }

    async with _http_client() as http:
        first = await http.post("/intercept", json=body, headers=headers)
        second = await http.post("/intercept", json=body, headers=headers)

    assert first.json()["span_id"] != second.json()["span_id"]
