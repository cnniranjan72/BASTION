"""Phase 1 milestone test (BUILD_PLAN.md): fire concurrent nested calls
through the real SDK against the real interceptor + real Postgres, and
prove the parent/child causal graph reconstructs correctly — strictly
increasing, gap-free, duplicate-free sequence numbers per trace even under
concurrent writers, and correct span parent/child linkage.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from uuid import UUID

import httpx
import pytest
from bastion import BastionBlockedError, BastionClient, current_span
from bastion_interceptor.db import db
from bastion_interceptor.main import app


def _make_client(agent_id: UUID, raw_key: str) -> BastionClient:
    return BastionClient(
        base_url="http://interceptor.test",
        api_key=raw_key,
        agent_id=agent_id,
        transport=httpx.ASGITransport(app=app),
    )


async def _noop() -> str:
    return "ok"


async def test_hardcoded_policy_allows_and_blocks(test_agent: tuple[UUID, str]) -> None:
    agent_id, raw_key = test_agent
    async with _make_client(agent_id, raw_key) as client:
        result = await client.call(
            "db.query", {"query": "SELECT 1", "database": "production"}, execute=_noop
        )
        assert result == "ok"

        with pytest.raises(BastionBlockedError):
            await client.call(
                "db.query",
                {"query": "DELETE FROM users", "database": "production"},
                execute=_noop,
            )


async def test_concurrent_nested_calls_reconstruct_causal_graph(
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, raw_key = test_agent
    num_children = 8
    captured: dict[str, UUID] = {}

    async def leaf_work() -> str:
        return "leaf-done"

    async def make_child(client: BastionClient, i: int) -> str:
        async def child_work() -> str:
            return await client.call(f"tool.grandchild.{i}", {"i": i}, leaf_work)

        return await client.call(f"tool.child.{i}", {"i": i}, child_work)

    async with _make_client(agent_id, raw_key) as client:

        async def root_work() -> list[str]:
            span = current_span()
            assert span is not None
            captured["trace_id"] = span.trace_id
            captured["root_span_id"] = span.span_id
            return await asyncio.gather(*(make_child(client, i) for i in range(num_children)))

        results = await client.call("tool.root", {}, root_work)
        assert results == ["leaf-done"] * num_children

    trace_id = captured["trace_id"]
    root_span_id = captured["root_span_id"]

    events = await db.get_events_for_trace(trace_id)

    # No duplicate/missing sequence numbers under concurrent writers on the
    # same trace: strictly 0..N-1, gap-free.
    seqs = [e["sequence_number"] for e in events]
    assert seqs == list(range(len(events)))

    by_span: dict[UUID, list[str]] = defaultdict(list)
    parent_of: dict[UUID, UUID | None] = {}
    for e in events:
        by_span[e["span_id"]].append(e["event_type"])
        parent_of[e["span_id"]] = e["parent_span_id"]

    expected_lifecycle = ["CallAttempted", "CallAllowed", "CallCompleted"]

    assert parent_of[root_span_id] is None
    assert by_span[root_span_id] == expected_lifecycle

    child_spans = [span for span, parent in parent_of.items() if parent == root_span_id]
    assert len(child_spans) == num_children

    grandchild_spans: list[UUID] = []
    for child_span in child_spans:
        assert by_span[child_span] == expected_lifecycle
        children_of_this = [s for s, p in parent_of.items() if p == child_span]
        assert len(children_of_this) == 1
        assert by_span[children_of_this[0]] == expected_lifecycle
        grandchild_spans.append(children_of_this[0])

    all_spans = {root_span_id, *child_spans, *grandchild_spans}
    assert set(by_span.keys()) == all_spans
    assert len(all_spans) == 1 + num_children + num_children
