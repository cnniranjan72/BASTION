"""BASTION.call(tool_name, args, execute) — ARCHITECTURE.md §2.1.

`execute` is only invoked if the interceptor allows the call: this is the
actual mechanism that *prevents* a blocked action from running, not just a
decision the caller could choose to ignore (PRD.md §5.1's "no prevention"
gap this product exists to close).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import UUID, uuid4

import httpx
from bastion_shared import InterceptRequest

from .context import SpanContext, current_span, reset_current_span, set_current_span
from .errors import BastionBlockedError, BastionPendingApprovalError

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
    ) -> None:
        self.agent_id = UUID(str(agent_id))
        self._http = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=timeout,
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

        intercept_request = InterceptRequest(
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            tool_name=tool_name,
            args=args,
            agent_id=self.agent_id,
        )
        response = await self._http.post(
            "/intercept", json=intercept_request.model_dump(mode="json")
        )
        response.raise_for_status()
        body = response.json()

        if body["decision"] == "blocked":
            raise BastionBlockedError(
                reason=body["reason"],
                span_id=UUID(body["span_id"]),
                policy_id=UUID(body["policy_id"]) if body.get("policy_id") else None,
            )
        if body["decision"] == "pending_approval":
            raise BastionPendingApprovalError(
                span_id=UUID(body["span_id"]),
                approval_request_id=UUID(body["approval_request_id"]),
            )

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
