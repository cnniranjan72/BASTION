"""bastion-shared — single source of truth for the BASTION wire schema.

Every service (interceptor, aggregator) and the Python SDK import these
models directly, so the event/policy/API shape cannot drift between them.
The frontend (TypeScript) currently mirrors these by hand instead
(`frontend/src/api/types.ts`) — a real, documented gap, not the original
plan — see docs/ARCHITECTURE.md §16 and docs/api/DRIFT.md.
"""

from .agents_api import AgentResponse, CreateAgentRequest, CreateAgentResponse, UpdateAgentRequest
from .approvals import ApprovalRequestResponse, ApprovalStatus
from .auth_api import LoginRequest, LogoutRequest, RefreshRequest, SignupRequest, TokenPairResponse
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
from .jwt_auth import (
    AccessTokenClaims,
    InvalidAccessToken,
    UserRole,
    decode_access_token,
    encode_access_token,
    load_key_file,
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
    "AgentResponse",
    "CreateAgentRequest",
    "CreateAgentResponse",
    "UpdateAgentRequest",
    "ApprovalRequestResponse",
    "ApprovalStatus",
    "LoginRequest",
    "LogoutRequest",
    "RefreshRequest",
    "SignupRequest",
    "TokenPairResponse",
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
    "AccessTokenClaims",
    "InvalidAccessToken",
    "UserRole",
    "decode_access_token",
    "encode_access_token",
    "load_key_file",
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
