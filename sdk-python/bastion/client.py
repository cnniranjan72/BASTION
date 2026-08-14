"""BASTION.call(tool_name, args, execute) — ARCHITECTURE.md §2.1.

`execute` is only invoked if the interceptor allows the call: this is the
actual mechanism that *prevents* a blocked action from running, not just a
decision the caller could choose to ignore (PRD.md §5.1's "no prevention"
gap this product exists to close).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import UUID, uuid4

import httpx
from bastion_shared import InterceptRequest

from .context import SpanContext, current_span, reset_current_span, set_current_span
from .errors import BastionBlockedError

T = TypeVar("T")


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


class BastionClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        agent_id: UUID | str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
        approval_max_wait: float = 60.0,
    ) -> None:
        self.agent_id = UUID(str(agent_id))
        # Overall client-side budget for a pending_approval call — past this,
        # treated as denied (fail closed) even if the server-side long-poll
        # in _wait_for_approval keeps returning "still pending".
        self._approval_max_wait = approval_max_wait
        self._http = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            # A pending_approval call legitimately blocks on GET
            # /approvals/{id}'s server-side long-poll (config.
            # approval_long_poll_seconds, ~25s by default) — the HTTP
            # client's own timeout must comfortably exceed that, independent
            # of the general request timeout used for /intercept itself.
            timeout=httpx.Timeout(timeout, read=max(timeout, approval_max_wait + 30.0)),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> BastionClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def call(
        self,
        tool_name: str,
        args: dict[str, Any],
        execute: Callable[[], Awaitable[T]],
    ) -> T:
        parent = current_span()
        trace_id = parent.trace_id if parent is not None else uuid4()
        parent_span_id = parent.span_id if parent is not None else None

        # U2 (v2 upgrade), UPGRADE_ARCHITECTURE.md §3: generated once per
        # logical call and reused verbatim across every retry below — this
        # is the actual mechanism that turns "agent retries a network
        # failure" into "same decision returned, no duplicate side effect"
        # instead of a second, independent /intercept evaluation.
        intercept_request = InterceptRequest(
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            tool_name=tool_name,
            args=args,
            agent_id=self.agent_id,
            idempotency_key=str(uuid4()),
        )
        body = await self._post_intercept_with_retry(intercept_request)

        if body["decision"] == "blocked":
            raise BastionBlockedError(
                reason=body["reason"],
                span_id=UUID(body["span_id"]),
                policy_id=UUID(body["policy_id"]) if body.get("policy_id") else None,
            )
        if body["decision"] == "pending_approval":
            # Blocks (via repeated calls to the server-side long-poll) until
            # approved, or raises BastionBlockedError for denied/timed_out/
            # this client's own budget exceeded — approved is the only path
            # that reaches the execute() call below.
            await self._wait_for_approval(body["poll_url"])

        span_id = UUID(body["span_id"])
        token = set_current_span(SpanContext(trace_id=trace_id, span_id=span_id))
        start = time.perf_counter()
        try:
            result = await execute()
        except Exception as exc:
            await self._report_completion(span_id, status="failed", start=start, error=str(exc))
            raise
        else:
            await self._report_completion(span_id, status="completed", start=start, result=result)
            return result
        finally:
            reset_current_span(token)

    async def _post_intercept_with_retry(
        self, intercept_request: InterceptRequest, *, max_attempts: int = 3
    ) -> dict[str, Any]:
        """Retries only network/transport failures (connection reset,
        timeout) — never an HTTP error response, which is a real decision
        from the interceptor (blocked, 4xx, 5xx) that retrying blindly
        wouldn't change. Safe to retry at all only because
        intercept_request.idempotency_key is fixed across every attempt:
        a retry after the first attempt's response was simply lost in
        transit returns that same original decision instead of evaluating
        the policy a second time."""
        last_error: httpx.TransportError | None = None
        for attempt in range(max_attempts):
            if attempt > 0:
                await asyncio.sleep(0.1 * (2**attempt))
            try:
                response = await self._http.post(
                    "/intercept", json=intercept_request.model_dump(mode="json")
                )
            except httpx.TransportError as exc:
                last_error = exc
                continue
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result
        assert last_error is not None
        raise last_error

    async def _wait_for_approval(self, poll_url: str) -> None:
        deadline = time.monotonic() + self._approval_max_wait
        while True:
            response = await self._http.get(poll_url)
            response.raise_for_status()
            body = response.json()
            status = body["status"]

            if status == "approved":
                return
            if status in ("denied", "timed_out"):
                raise BastionBlockedError(
                    reason=f"approval {status}", span_id=UUID(body["span_id"]), policy_id=None
                )
            # Still "pending": GET /approvals/{id} already blocked server-side
            # for a while (its own long-poll window) before returning this —
            # only loop again if there's budget left, otherwise fail closed.
            if time.monotonic() >= deadline:
                raise BastionBlockedError(
                    reason="approval timed out waiting for a decision",
                    span_id=UUID(body["span_id"]),
                    policy_id=None,
                )

    async def _report_completion(
        self,
        span_id: UUID,
        *,
        status: str,
        start: float,
        result: Any | None = None,
        error: str | None = None,
    ) -> None:
        latency_ms = (time.perf_counter() - start) * 1000
        payload = {
            "status": status,
            "latency_ms": latency_ms,
            "result": _json_safe(result),
            "error": error,
        }
        response = await self._http.post(f"/spans/{span_id}/complete", json=payload)
        response.raise_for_status()
