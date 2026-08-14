// Hand-written, mirroring bastion_shared's Pydantic models field-for-field.
//
// ARCHITECTURE.md §7 originally planned generating these from the
// FastAPI/OpenAPI schema rather than hand-duplicating them. That's still the
// right long-term answer (see docs/ARCHITECTURE.md §16) — shipped
// hand-written for Phase 7 given time constraints. No business logic lives
// here, only wire shapes, so drift is a type-checking annoyance to fix, not
// a silent correctness bug.

export type UserRole = "owner" | "admin" | "approver" | "viewer";

export interface TokenPairResponse {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  role: UserRole;
}

export interface TeamMember {
  id: string;
  org_id: string;
  email: string;
  role: UserRole;
  created_at: string;
}

export interface CreateUserResponse extends TeamMember {
  temporary_password: string;
}

export interface ApiToken {
  id: string;
  name: string;
  token_prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface CreateApiTokenResponse extends ApiToken {
  token: string;
}

export type NodeStatus =
  "pending" | "allowed" | "blocked" | "pending_approval" | "completed" | "failed";

export type TraceStatus = "running" | "completed" | "failed" | "had_blocks";

export interface GraphNode {
  span_id: string;
  parent_span_id: string | null;
  tool_name: string;
  status: NodeStatus;
  args: Record<string, unknown> | null;
  latency_ms: number | null;
  cost: number | null;
  reason: string | null;
}

export interface GraphEdge {
  from: string;
  to: string;
}

export interface TraceGraph {
  trace_id: string;
  agent_id: string;
  status: TraceStatus;
  total_cost: number;
  total_calls: number;
  blocked_calls: number;
  started_at: string;
  ended_at: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface TraceSummary {
  trace_id: string;
  agent_id: string;
  status: TraceStatus;
  total_cost: number;
  total_calls: number;
  blocked_calls: number;
  started_at: string;
  ended_at: string | null;
}

export interface RawEvent {
  event_id: string;
  trace_id: string;
  span_id: string;
  parent_span_id: string | null;
  agent_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  sequence_number: number;
  created_at: string;
}

export interface Agent {
  id: string;
  org_id: string;
  name: string;
  policy_set_id: string | null;
  created_at: string;
}

export interface CreateAgentResponse extends Agent {
  api_key: string;
}

export interface ApprovalRequest {
  id: string;
  trace_id: string;
  span_id: string;
  status: "pending" | "approved" | "denied" | "timed_out";
  requested_at: string;
  resolved_by: string | null;
  resolved_at: string | null;
}

export interface PolicyRuleMatch {
  tool: string;
  pattern?: string;
  database?: string;
}

export interface PolicyRule {
  match: PolicyRuleMatch;
  action: "allow" | "block" | "require_approval";
  condition?: string;
}

export interface Policy {
  id: string;
  org_id: string;
  policy_set_id: string;
  name: string;
  version: number;
  definition: PolicyRule[];
  active: boolean;
  created_at: string;
}

// WS /live/{agent_id} messages — realtime.py.
export type LiveMessage =
  | { type: "node_added"; node: { span_id: string; tool_name: string; status: "pending" } }
  | {
      type: "node_updated";
      span_id: string;
      status: Exclude<NodeStatus, "pending">;
      latency_ms?: number;
      cost?: number;
      reason?: string | null;
    }
  | { type: "edge_added"; from: string; to: string };

export interface ErrorResponse {
  error: { code: string; message: string; request_id: string };
}
