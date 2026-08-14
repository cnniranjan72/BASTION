"""U9 milestone test (UPGRADE_BUILD_PLAN.md): assert a large payload
round-trips correctly through object storage via its pointer.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from bastion_interceptor import object_storage
from bastion_interceptor.config import config
from bastion_interceptor.db import db


def _large_payload(marker: str) -> dict:
    # Comfortably over the default 8KB threshold once serialized, whatever
    # the length of `marker` — padding alone (not `marker * N`, which
    # shrinks with a longer marker for the same N) guarantees this.
    return {"tool_name": "big.tool", "args": {"blob": marker + ("x" * 20_000)}}


async def test_small_payload_stays_inline() -> None:
    small = {"tool_name": "tiny.tool", "args": {"amount": 5}}
    result = await object_storage.upload_if_large(small)
    assert result == small
    assert "storage" not in result


async def test_large_payload_becomes_a_pointer() -> None:
    large = _large_payload("a")
    result = await object_storage.upload_if_large(large)
    assert result["storage"] == "s3"
    assert result["uri"].startswith(f"s3://{config.object_storage_bucket}/payloads/")
    assert result["size_bytes"] > config.object_storage_payload_threshold_bytes
    assert len(result["hash"]) == 64  # sha256 hex digest


async def test_pointer_round_trips_back_to_the_original_payload() -> None:
    large = _large_payload("b")
    pointer = await object_storage.upload_if_large(large)
    resolved = await object_storage.resolve_payload(pointer)
    assert resolved == large


async def test_resolve_payload_passes_through_non_pointer_unchanged() -> None:
    small = {"tool_name": "tiny.tool", "args": {}}
    resolved = await object_storage.resolve_payload(small)
    assert resolved == small


async def test_identical_large_payload_deduplicates_to_the_same_object() -> None:
    payload = _large_payload("dedup-marker")
    first = await object_storage.upload_if_large(payload)
    second = await object_storage.upload_if_large(payload)
    assert first["hash"] == second["hash"]
    assert first["uri"] == second["uri"]

    # Not just equal pointers — genuinely the same object, uploaded once.
    # A HEAD after both calls confirms one object exists at that key;
    # combined with content-addressing (the key IS the hash), two
    # identical payloads can only ever resolve to the same single object.
    key = first["uri"].removeprefix(f"s3://{config.object_storage_bucket}/")
    async with object_storage._session().client("s3", **object_storage._client_kwargs()) as s3:
        head = await s3.head_object(Bucket=config.object_storage_bucket, Key=key)
    assert head["ContentLength"] == first["size_bytes"]


async def test_large_call_attempted_payload_round_trips_through_insert_event(
    test_agent: tuple[UUID, str],
) -> None:
    """End to end: db.insert_event offloads it, the row actually stored in
    Postgres is a small pointer (not the huge original), and
    db.get_call_attempted_payload transparently returns the real payload
    back — proving the write and read halves both work together, not just
    the standalone object_storage functions in isolation."""
    from bastion_shared import EventType

    agent_id, _ = test_agent
    trace_id = uuid.uuid4()
    span_id = uuid.uuid4()
    large_args = {"document_text": "x" * 20_000}

    await db.insert_event(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=None,
        agent_id=agent_id,
        event_type=EventType.CALL_ATTEMPTED,
        payload={"tool_name": "documents.read", "args": large_args},
    )

    # db.pool, not a fresh asyncpg.connect() — the pool has the jsonb ->
    # dict type codec registered (db.py's _init_connection); a raw
    # connection would return the jsonb column as an undecoded JSON string.
    stored_payload = await db.pool.fetchval(
        "SELECT payload FROM events WHERE span_id = $1 AND event_type = 'CallAttempted'", span_id
    )
    assert stored_payload["storage"] == "s3"
    assert "document_text" not in str(stored_payload)

    resolved = await db.get_call_attempted_payload(span_id)
    assert resolved is not None
    assert resolved["tool_name"] == "documents.read"
    assert resolved["args"] == large_args
