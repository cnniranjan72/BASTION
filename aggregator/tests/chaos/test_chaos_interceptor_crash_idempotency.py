"""U14 chaos scenario (UPGRADE_ARCHITECTURE.md §16): "Kill interceptor
mid-request" — required invariant: "client sees a clean error or a
fully-completed idempotent retry — never a half-applied state."

Methodology note (applies to every file in this package, stated once here):
faults are injected at the precise mechanism boundary a real crash would
leave behind, rather than via a real OS-level SIGKILL raced against network
I/O timing. A raw process kill can land at any instruction and would make
this test non-deterministic and frequently pass by accident without ever
exercising the interesting boundary. `try_reserve_idempotency_key` below is
*exactly* the DB mutation `_intercept()` performs immediately before
starting real decision work (interceptor/src/bastion_interceptor/main.py) —
calling it directly and then never calling `complete_idempotency_key`
reproduces, deterministically, precisely the Postgres state a process death
between those two calls would leave. This is the harder, previously-untested
half of the invariant: the "fully-completed idempotent retry" half is
already covered by
interceptor/tests/test_idempotency.py::test_sequential_retry_with_same_key_returns_identical_response.

Real finding from writing this test: a retry against an orphaned
reservation doesn't self-heal. `_await_idempotent_result` polls for
`status == "completed"` for up to 2 seconds and then the caller raises a
clean 503 `IDEMPOTENT_REQUEST_IN_PROGRESS` — technically satisfying the
invariant (a clean error, never a half-applied state), but every retry
after that gets the *same* 503 forever: nothing ever marks an orphaned
reservation as abandoned or lets a later request win it. This is a real,
accepted limitation, not fixed in this phase (no reaper/expiry mechanism
exists for idempotency_keys rows) — documented here and in
docs/CHAOS_RESULTS.md rather than silently left unstated.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import httpx
from bastion_interceptor.db import db as interceptor_db
from bastion_interceptor.main import app as interceptor_app


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=interceptor_app), base_url="http://interceptor.test"
    )


async def test_retry_against_orphaned_reservation_gets_clean_error_not_half_applied_state(
    test_org: UUID,
    test_agent: tuple[UUID, str],
) -> None:
    agent_id, raw_key = test_agent
    trace_id = uuid.uuid4()
    span_id = uuid.uuid4()
    idempotency_key = str(uuid.uuid4())

    # Reproduces exactly the Postgres state left by an interceptor process
    # killed after reserving this idempotency key but before completing it
    # — see module docstring.
    reservation = await interceptor_db.try_reserve_idempotency_key(
        org_id=test_org,
        agent_id=agent_id,
        idempotency_key=idempotency_key,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=None,
    )
    assert reservation is not None, "test setup: reservation should succeed against a fresh key"

    body = {
        "trace_id": str(trace_id),
        "parent_span_id": None,
        "tool_name": "chaos.crash_test",
        "args": {},
        "agent_id": str(agent_id),
        "idempotency_key": idempotency_key,
    }
    headers = {"Authorization": f"Bearer {raw_key}"}

    async with _http_client() as http:
        response = await http.post("/intercept", json=body, headers=headers)

    # The invariant: a clean, well-formed error — not a hang, not a 500,
    # not a corrupted/partial decision.
    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "IDEMPOTENT_REQUEST_IN_PROGRESS"

    # Never a half-applied state: no decision was ever recorded for this
    # idempotency key's span — the orphaned reservation blocked evaluation
    # entirely rather than letting it partially proceed.
    events = await interceptor_db.get_events_for_trace(trace_id)
    assert events == [], f"expected zero events for an orphaned-reservation trace, got {events}"

    # Documents the known limitation from the module docstring: the retry
    # doesn't self-heal, it gets the identical clean error again.
    async with _http_client() as http:
        second_response = await http.post("/intercept", json=body, headers=headers)
    assert second_response.status_code == 503
    assert second_response.json()["error"]["code"] == "IDEMPOTENT_REQUEST_IN_PROGRESS"
