import { useAuthStore } from "../store/auth";
import type {
  Agent,
  AgentHealth,
  ApiToken,
  ApprovalRequest,
  CatalogItem,
  CommandCenterSnapshot,
  CostSummary,
  CreateAgentResponse,
  CreateApiTokenResponse,
  CreateUserResponse,
  ErrorResponse,
  LiveDemoRunResponse,
  LlmCredential,
  LlmProvider,
  Policy,
  PolicyPropagationResponse,
  RawEvent,
  SimulatePolicyResponse,
  TeamMember,
  ThreatSummary,
  TokenPairResponse,
  TraceFilters,
  TraceGraph,
  TraceSummary,
  UserRole,
} from "./types";

// vite.config.ts proxies these to the interceptor (4001) / aggregator
// (4002) in dev; in production both would sit behind the same reverse
// proxy path prefixes.
const INTERCEPTOR_BASE = "/api/interceptor";
const AGGREGATOR_BASE = "/api/aggregator";
const CATALOG_BASE = "/api/catalog";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

let refreshInFlight: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  const { refreshToken, setTokens, logout } = useAuthStore.getState();
  if (!refreshToken) return false;
  const response = await fetch(`${INTERCEPTOR_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!response.ok) {
    logout();
    return false;
  }
  const body = (await response.json()) as TokenPairResponse;
  setTokens(body);
  return true;
}

// Refresh tokens are one-time-use (Phase 5) — if two requests 401
// simultaneously, only one may actually call /auth/refresh or the second's
// token gets consumed by the first and reuse-detection revokes the whole
// family. Concurrent callers share one in-flight refresh.
async function refreshOnce(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = doRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const attempt = async (): Promise<Response> => {
    const { accessToken } = useAuthStore.getState();
    return fetch(`${base}${path}`, {
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...init?.headers,
      },
    });
  };

  let response = await attempt();
  if (response.status === 401 && useAuthStore.getState().refreshToken) {
    const refreshed = await refreshOnce();
    if (refreshed) response = await attempt();
  }

  if (!response.ok) {
    let body: ErrorResponse | null = null;
    try {
      body = (await response.json()) as ErrorResponse;
    } catch {
      // non-JSON error body; fall through with a generic message
    }
    throw new ApiError(
      response.status,
      body?.error?.code ?? "UNKNOWN_ERROR",
      body?.error?.message ?? response.statusText,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<TokenPairResponse>(INTERCEPTOR_BASE, "/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  signup: (orgName: string, email: string, password: string) =>
    request<TokenPairResponse>(INTERCEPTOR_BASE, "/auth/signup", {
      method: "POST",
      body: JSON.stringify({ org_name: orgName, email, password }),
    }),
  logout: (refresh_token: string) =>
    request<{ status: string }>(INTERCEPTOR_BASE, "/auth/logout", {
      method: "POST",
      body: JSON.stringify({ refresh_token }),
    }),
  changePassword: (current_password: string, new_password: string) =>
    request<{ status: string }>(INTERCEPTOR_BASE, "/auth/password", {
      method: "PATCH",
      body: JSON.stringify({ current_password, new_password }),
    }),

  listApiTokens: () => request<ApiToken[]>(INTERCEPTOR_BASE, "/api-tokens"),
  createApiToken: (name: string) =>
    request<CreateApiTokenResponse>(INTERCEPTOR_BASE, "/api-tokens", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  revokeApiToken: (id: string) =>
    request<void>(INTERCEPTOR_BASE, `/api-tokens/${id}`, { method: "DELETE" }),

  // U17 (BYOK — `docs/adr/ADR-022`).
  listLlmCredentials: () => request<LlmCredential[]>(INTERCEPTOR_BASE, "/llm-keys"),
  createLlmCredential: (provider: LlmProvider, label: string, api_key: string) =>
    request<LlmCredential>(INTERCEPTOR_BASE, "/llm-keys", {
      method: "POST",
      body: JSON.stringify({ provider, label, api_key }),
    }),
  revokeLlmCredential: (id: string) =>
    request<void>(INTERCEPTOR_BASE, `/llm-keys/${id}`, { method: "DELETE" }),
  runLiveDemo: (provider: LlmProvider | "ollama", credentialId: string | null) =>
    request<LiveDemoRunResponse>(INTERCEPTOR_BASE, "/demo/live-run", {
      method: "POST",
      body: JSON.stringify({ provider, credential_id: credentialId }),
    }),

  listUsers: () => request<TeamMember[]>(INTERCEPTOR_BASE, "/users"),
  createUser: (email: string, role: UserRole) =>
    request<CreateUserResponse>(INTERCEPTOR_BASE, "/users", {
      method: "POST",
      body: JSON.stringify({ email, role }),
    }),
  updateUserRole: (userId: string, role: UserRole) =>
    request<TeamMember>(INTERCEPTOR_BASE, `/users/${userId}/role`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),

  listAgents: () => request<Agent[]>(INTERCEPTOR_BASE, "/agents"),
  createAgent: (name: string, policy_set_id: string | null) =>
    request<CreateAgentResponse>(INTERCEPTOR_BASE, "/agents", {
      method: "POST",
      body: JSON.stringify({ name, policy_set_id }),
    }),
  updateAgentPolicySet: (agentId: string, policy_set_id: string | null) =>
    request<Agent>(INTERCEPTOR_BASE, `/agents/${agentId}`, {
      method: "PATCH",
      body: JSON.stringify({ policy_set_id }),
    }),

  listPolicies: () => request<Policy[]>(INTERCEPTOR_BASE, "/policies"),
  createPolicy: (name: string, definition: Policy["definition"], basedOnVersion?: number) =>
    request<Policy>(INTERCEPTOR_BASE, "/policies", {
      method: "POST",
      body: JSON.stringify({ name, definition, based_on_version: basedOnVersion ?? null }),
    }),
  activatePolicy: (id: string) =>
    request<Policy>(INTERCEPTOR_BASE, `/policies/${id}/activate`, { method: "POST" }),
  simulatePolicy: (agentId: string, toolName: string, args: Record<string, unknown>) =>
    request<SimulatePolicyResponse>(INTERCEPTOR_BASE, "/policies/simulate", {
      method: "POST",
      body: JSON.stringify({ agent_id: agentId, tool_name: toolName, args }),
    }),
  getPolicyPropagation: (policySetId: string) =>
    request<PolicyPropagationResponse>(INTERCEPTOR_BASE, `/policies/${policySetId}/propagation`),

  listApprovals: () => request<ApprovalRequest[]>(INTERCEPTOR_BASE, "/approvals"),
  approve: (id: string) =>
    request<ApprovalRequest>(INTERCEPTOR_BASE, `/approvals/${id}/approve`, { method: "POST" }),
  deny: (id: string) =>
    request<ApprovalRequest>(INTERCEPTOR_BASE, `/approvals/${id}/deny`, { method: "POST" }),

  // U16 (v2 upgrade): filters are all optional and combinable, `docs/adr/ADR-021`.
  listTraces: (filters?: TraceFilters) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters ?? {})) {
      if (value) params.set(key, value);
    }
    const query = params.toString();
    return request<TraceSummary[]>(AGGREGATOR_BASE, `/traces${query ? `?${query}` : ""}`);
  },
  getTrace: (traceId: string) => request<TraceGraph>(AGGREGATOR_BASE, `/traces/${traceId}`),
  getTraceEvents: (traceId: string) =>
    request<RawEvent[]>(AGGREGATOR_BASE, `/traces/${traceId}/events`),

  // U16 (v2 upgrade) — Threat Center / Agent Health / Cost Center / Command
  // Center, `docs/adr/ADR-021`.
  getThreats: (windowDays = 30) =>
    request<ThreatSummary>(AGGREGATOR_BASE, `/threats?window_days=${windowDays}`),
  // Track 01: the agent-readable merchant catalog the buyer demo (and
  // razorpay.purchase itself) reads from — GET /catalog, unauthenticated
  // on the catalog service's own side, no governance-core dependency.
  listCatalog: () => request<CatalogItem[]>(CATALOG_BASE, "/catalog"),
  getAgentHealth: (agentId: string, windowDays = 30) =>
    request<AgentHealth>(AGGREGATOR_BASE, `/agents/${agentId}/health?window_days=${windowDays}`),
  getCosts: (windowDays = 30) =>
    request<CostSummary>(AGGREGATOR_BASE, `/costs?window_days=${windowDays}`),
  getCommandCenterSnapshot: (windowDays = 1) =>
    request<CommandCenterSnapshot>(
      AGGREGATOR_BASE,
      `/command-center?window_days=${windowDays}`,
    ),
};

export function liveWebSocketUrl(agentId: string): string {
  const { accessToken } = useAuthStore.getState();
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/live/${agentId}?token=${encodeURIComponent(accessToken ?? "")}`;
}
