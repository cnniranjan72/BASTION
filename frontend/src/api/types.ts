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

// U17 (BYOK — `docs/adr/ADR-022`). key_ciphertext/key_nonce are never sent
// to the frontend at all — only key_last4, same shape as ApiToken's
// token_prefix-not-token_hash pattern above.
export type LlmProvider = "openai" | "anthropic" | "gemini";

export interface LlmCredential {
  id: string;
  provider: LlmProvider;
  label: string;
  key_last4: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface LiveDemoStep {
  tool_name: string;
  args: Record<string, unknown>;
  decision: "allowed" | "blocked" | "pending_approval";
  reason: string | null;
  result: unknown;
}

export interface LiveDemoRunResponse {
  trace_id: string;
  provider: LlmProvider | "ollama";
  steps: LiveDemoStep[];
  final_text: string | null;
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
  // Not part of the backend's GraphNode wire shape (a TraceGraph's nodes
  // don't repeat their own trace_id) — stamped on client-side from the
  // owning TraceGraph.trace_id (loadSnapshot) or from the live message
  // that produced/touched the node (applyLiveMessage, U15/v2). An agent
  // can have more than one concurrent trace, so the inspector needs this
  // to show/link the right one.
  trace_id: string | null;
  // Client-side receive timestamp, not a backend field — see
  // store/graph.ts's TimelineEntry comment for why live messages don't
  // carry a server timestamp today.
  updated_at: number | null;
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

// Track 01: catalog/src/bastion_catalog/data.py's CatalogItem, unchanged.
export interface CatalogItem {
  sku: string;
  name: string;
  price_inr: number;
  description: string;
  stock: number;
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

// U6/U15 (v2 upgrade) — UPGRADE_ARCHITECTURE.md §8's stateful governance
// dimensions, `docs/adr/ADR-015`.
export interface PolicyLimits {
  calls_per_minute?: number | null;
  max_transaction_amount?: number | null;
  org_spend_per_day?: number | null;
  agent_llm_budget_per_hour?: number | null;
}

export interface PolicyRule {
  match: PolicyRuleMatch;
  action: "allow" | "block" | "require_approval";
  condition?: string;
  limits?: PolicyLimits | null;
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

// U15 (v2 upgrade) — Policy Studio's simulator, `docs/adr/ADR-020`.
export interface SimulatePolicyResponse {
  decision: "allow" | "block" | "require_approval";
  reason: string | null;
  policy_id: string | null;
  policy_set_id: string | null;
  matched_rule_tool: string | null;
  configured_limits: PolicyLimits | null;
}

// U15 (v2 upgrade) — Policy Studio's propagation-status panel,
// `docs/adr/ADR-020`. `known_interceptor_instances` is honestly always 1 —
// see the ADR for why this deployment has no multi-replica registry.
export interface PolicyPropagationResponse {
  policy_set_id: string;
  active_version: number;
  active_policy_id: string;
  this_instance_cached_version: number | null;
  propagated: boolean;
  known_interceptor_instances: number;
}

// WS /live/{agent_id} messages — realtime.py. trace_id added U15 (v2
// upgrade) — see GraphNode's comment above for why.
export type LiveMessage =
  | {
      type: "node_added";
      trace_id: string;
      node: { span_id: string; tool_name: string; status: "pending" };
    }
  | {
      type: "node_updated";
      trace_id: string;
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

// U16 (v2 upgrade) — the 4 new analytics endpoints, `docs/adr/ADR-021`.
// Every field is a real aggregate; ADR-021 documents the handful of places
// FRONTEND_V2.md's own mock text isn't literally something this system
// tracks (e.g. "availability" = real call-success rate, not infra uptime).

export interface PolicyViolationCount {
  policy_id: string;
  policy_name: string;
  block_count: number;
}

export interface ThreatTimelineBucket {
  day: string;
  blocked_count: number;
}

export interface ThreatSummary {
  window_days: number;
  blocked_calls_total: number;
  top_violated_policies: PolicyViolationCount[];
  timeline: ThreatTimelineBucket[];
}

export interface ToolCount {
  tool_name: string;
  count: number;
}

export interface AnomalyFlag {
  description: string;
}

export interface AgentHealth {
  agent_id: string;
  agent_name: string;
  window_days: number;
  calls_total: number;
  blocked_total: number;
  failed_total: number;
  pending_approval_total: number;
  avg_latency_ms: number | null;
  estimated_cost_total: number;
  top_tools: ToolCount[];
  health_score: number;
  reliability: number;
  policy_compliance: number;
  tool_error_rate: number;
  approval_rate: number;
  anomalies: AnomalyFlag[];
}

export interface CostByAgent {
  agent_id: string;
  agent_name: string;
  cost: number;
}

export interface CostByTool {
  tool_name: string;
  cost: number;
}

export interface CostSummary {
  window_days: number;
  total_cost: number;
  by_agent: CostByAgent[];
  by_tool: CostByTool[];
  estimated_savings_from_policy_enforcement: number;
}

export interface LiveActivityEntry {
  agent_id: string;
  agent_name: string;
  tool_name: string;
  decision: string;
  at: string;
}

export interface CommandCenterSnapshot {
  agents_total: number;
  agents_healthy: number;
  availability_pct: number;
  window_days: number;
  last_incident_at: string | null;
  recent_activity: LiveActivityEntry[];
}

// U16 (v2 upgrade) — Trace Explorer's GET /traces filters.
export interface TraceFilters {
  agent_id?: string;
  status?: TraceStatus;
  tool?: string;
  policy?: string;
  started_after?: string;
  started_before?: string;
}
