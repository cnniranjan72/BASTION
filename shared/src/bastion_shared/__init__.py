"""bastion-shared — single source of truth for the BASTION wire schema.

Every service (interceptor, aggregator) and the Python SDK import these
models directly, so the event/policy/API shape cannot drift between them.
The frontend (TypeScript) is kept in sync separately, by generating types
from the FastAPI-produced OpenAPI schema rather than duplicating models by
hand — see docs/ARCHITECTURE.md §7 (Language & schema decisions).
"""

from .approvals import ApprovalRequestResponse, ApprovalStatus
from .errors import BastionError, ErrorDetail, ErrorResponse
from .events import (
    CallAttemptedPayload,
    CallOutcomePayload,
    Event,
    EventType,
    NewEvent,
    PolicyDecisionPayload,
)
from .graph import GraphEdge, GraphNode, NodeStatus, TraceGraph, TraceStatus, TraceSummaryResponse
from .intercept import (
    InterceptAllowedResponse,
    InterceptBlockedResponse,
    InterceptPendingResponse,
    InterceptRequest,
    InterceptResponse,
)
from .policy import Policy, PolicyDefinition, PolicyMatch, PolicyRule
from .policy_api import CreatePolicyRequest, PolicyResponse
from .realtime import (
    EdgeAddedMessage,
    LiveMessage,
    LiveNode,
    NodeAddedMessage,
    NodeUpdatedMessage,
)
from .spans import CompleteSpanRequest, CompleteSpanResponse

__all__ = [
    "ApprovalRequestResponse",
    "ApprovalStatus",
    "BastionError",
    "ErrorDetail",
    "ErrorResponse",
    "CallAttemptedPayload",
    "CallOutcomePayload",
    "Event",
    "EventType",
    "NewEvent",
    "PolicyDecisionPayload",
    "GraphEdge",
    "GraphNode",
    "NodeStatus",
    "TraceGraph",
    "TraceStatus",
    "TraceSummaryResponse",
    "InterceptAllowedResponse",
    "InterceptBlockedResponse",
    "InterceptPendingResponse",
    "InterceptRequest",
    "InterceptResponse",
    "Policy",
    "PolicyDefinition",
    "PolicyMatch",
    "PolicyRule",
    "CreatePolicyRequest",
    "PolicyResponse",
    "CompleteSpanRequest",
    "CompleteSpanResponse",
    "EdgeAddedMessage",
    "LiveMessage",
    "LiveNode",
    "NodeAddedMessage",
    "NodeUpdatedMessage",
]
