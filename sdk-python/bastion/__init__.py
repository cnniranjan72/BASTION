"""BASTION client SDK.

Thin wrapper per ARCHITECTURE.md §2.1: `BASTION.call(tool_name, payload, context)`
replaces a direct API/DB call, routing it through the interceptor instead.
Request/response shapes come from `bastion_shared` — the SDK never redefines
the wire schema, it imports it.

Trace/span propagation is automatic (see context.py) — nested `call()`s
inside an `execute` callback inherit the right parent without the caller
threading trace_id/span_id through by hand.
"""

from .client import BastionClient
from .context import SpanContext, current_span
from .errors import BastionBlockedError, BastionPendingApprovalError

__version__ = "0.1.0"

__all__ = [
    "BastionClient",
    "SpanContext",
    "current_span",
    "BastionBlockedError",
    "BastionPendingApprovalError",
]
