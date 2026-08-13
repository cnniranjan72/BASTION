"""Phase 4 milestone (BUILD_PLAN.md): pull up any past trace via API and
get a complete, correctly-ordered causal graph as JSON.

Cross-service test by necessity: real trace data is generated through the
*interceptor's* app (the only thing that can write events), then replayed
through the *aggregator's* app (the only thing that reads trace_summaries/
folds events for the API) — both against the same Postgres, exactly as they
would be as two separate deployed services.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from bastion import BastionClient, current_span
from bastion_aggregator.db import db
from bastion_aggregator.main import app as aggregator_app
from bastion_interceptor.main import app as interceptor_app


def _bastion_client(agent_id: UUID, raw_key: str) -> BastionClient:
    return BastionClient(
        base_url="http://interceptor.test",
        api_key=raw_key,
        agent_id=agent_id,
        transport=httpx.ASGITransport(app=interceptor_app),
    )


def _aggregator_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=aggregator_app), base_url="http://aggregator.test"
    )


def _auth_headers(login: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}"}


async def _generate_trace(agent_id: UUID, raw_key: str) -> UUID:
    """Root call with two concurrent children — same causal shape as the
    Phase 1 milestone test, small enough to keep this test fast."""
    captured: dict[str, UUID] = {}

    async def leaf() -> str:
        return "done"

    async def child(client: BastionClient, i: int) -> str:
        return await client.call(f"tool.child.{i}", {"i": i}, leaf)

    async with _bastion_client(agent_id, raw_key) as client:

        async def root_work() -> list[str]:
            span = current_span()
            assert span is not None
            captured["trace_id"] = span.trace_id
            return await asyncio.gather(*(child(client, i) for i in range(2)))

        await client.call("tool.root", {}, root_work)

    return captured["trace_id"]


async def _wait_for_persisted_trace(
    trace_id: UUID, login: dict[str, str], deadline_seconds: float = 5.0
) -> dict:
    """The aggregator persists trace_summaries asynchronously, off a
    LISTEN/NOTIFY notification — not synchronously with the intercept calls
    above, so the test polls rather than asserting immediately."""
    deadline = time.monotonic() + deadline_seconds
    async with _aggregator_client() as http:
        while time.monotonic() < deadline:
            response = await http.get(f"/traces/{trace_id}", headers=_auth_headers(login))
            if response.status_code == 200 and response.json()["status"] != "running":
                return response.json()
            await asyncio.sleep(0.05)
    raise AssertionError("trace was not persisted as terminal in time")


async def test_replay_reconstructs_causal_graph(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
) -> None:
    agent_id, raw_key = test_agent
    login = await login_as(await make_user(role="viewer"))
    trace_id = await _generate_trace(agent_id, raw_key)

    graph = await _wait_for_persisted_trace(trace_id, login)

    assert graph["trace_id"] == str(trace_id)
    assert graph["agent_id"] == str(agent_id)
    assert graph["status"] == "completed"
    assert graph["total_calls"] == 3  # root + 2 children
    assert graph["blocked_calls"] == 0
    assert graph["ended_at"] is not None

    nodes_by_span = {n["span_id"]: n for n in graph["nodes"]}
    assert len(nodes_by_span) == 3
    for node in nodes_by_span.values():
        assert node["status"] == "completed"
        assert node["latency_ms"] is not None

    root_nodes = [n for n in graph["nodes"] if n["parent_span_id"] is None]
    assert len(root_nodes) == 1
    assert root_nodes[0]["tool_name"] == "tool.root"

    assert len(graph["edges"]) == 2
    for edge in graph["edges"]:
        assert edge["from"] == root_nodes[0]["span_id"]
        assert edge["to"] in nodes_by_span


async def test_get_trace_events_returns_raw_event_list(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
) -> None:
    agent_id, raw_key = test_agent
    login = await login_as(await make_user(role="viewer"))
    trace_id = await _generate_trace(agent_id, raw_key)
    await _wait_for_persisted_trace(trace_id, login)

    async with _aggregator_client() as http:
        response = await http.get(f"/traces/{trace_id}/events", headers=_auth_headers(login))
    events = response.json()

    assert response.status_code == 200
    assert len(events) > 0
    assert all(e["trace_id"] == str(trace_id) for e in events)
    seqs = [e["sequence_number"] for e in events]
    assert seqs == sorted(seqs)


async def test_list_traces_scoped_by_org(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
) -> None:
    agent_id, raw_key = test_agent
    same_org_login = await login_as(await make_user(role="viewer"))
    trace_id = await _generate_trace(agent_id, raw_key)
    await _wait_for_persisted_trace(trace_id, same_org_login)

    other_org = await db.pool.fetchval(
        "INSERT INTO organizations (id, name) VALUES (gen_random_uuid(), 'org-b') RETURNING id"
    )
    other_org_login = await login_as(await make_user(role="viewer", org_id=other_org))

    async with _aggregator_client() as http:
        same_org = await http.get("/traces", headers=_auth_headers(same_org_login))
        other_org_response = await http.get("/traces", headers=_auth_headers(other_org_login))

    assert str(trace_id) in [t["trace_id"] for t in same_org.json()]
    assert other_org_response.json() == []


async def test_replay_before_completion_folds_live(
    test_agent: tuple[UUID, str],
    make_user: Callable[..., Awaitable[dict]],
    login_as: Callable[[dict], Awaitable[dict]],
) -> None:
    """GET /traces/{id} must work for an in-progress trace too — event
    sourcing means current state is always derivable, not just once a
    projection has been persisted."""
    agent_id, raw_key = test_agent
    login = await login_as(await make_user(role="viewer"))
    started = asyncio.Event()
    finish = asyncio.Event()
    captured: dict[str, UUID] = {}

    async def slow_root() -> str:
        span = current_span()
        assert span is not None
        captured["trace_id"] = span.trace_id
        started.set()
        await finish.wait()
        return "done"

    async with _bastion_client(agent_id, raw_key) as client:
        call_task = asyncio.create_task(client.call("tool.slow", {}, slow_root))
        await asyncio.wait_for(started.wait(), timeout=5)

        # Give the LISTEN/NOTIFY handler a moment to process CallAttempted.
        deadline = time.monotonic() + 3
        graph = None
        async with _aggregator_client() as http:
            while time.monotonic() < deadline:
                response = await http.get(
                    f"/traces/{captured['trace_id']}", headers=_auth_headers(login)
                )
                if response.status_code == 200:
                    graph = response.json()
                    break
                await asyncio.sleep(0.05)

        assert graph is not None
        assert graph["status"] == "running"
        assert graph["ended_at"] is None
        assert len(graph["nodes"]) == 1

        finish.set()
        await call_task
