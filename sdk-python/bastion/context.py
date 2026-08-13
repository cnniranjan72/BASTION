"""Automatic trace/span propagation via contextvars — ARCHITECTURE.md §2.1:
"Injects trace_id (new if root call, inherited if nested) and span_id (new
per call, parent_span_id set)." Using a contextvar instead of requiring the
caller to thread trace_id/parent_span_id through manually means nested
`bastion.call()` invocations inside an `execute` callback automatically pick
up the right parent — and asyncio.gather'd concurrent children each get
their own copy of the context at task-creation time, so they don't stomp on
each other's span while all correctly seeing the same parent.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class SpanContext:
    trace_id: UUID
    span_id: UUID


_current_span: ContextVar[SpanContext | None] = ContextVar("bastion_current_span", default=None)


def current_span() -> SpanContext | None:
    return _current_span.get()


def set_current_span(span: SpanContext | None) -> Token[SpanContext | None]:
    return _current_span.set(span)


def reset_current_span(token: Token[SpanContext | None]) -> None:
    _current_span.reset(token)
