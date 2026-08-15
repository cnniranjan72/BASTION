import { useAuthStore } from "../store/auth";
import type {
  Agent,
  ApiToken,
  ApprovalRequest,
  CreateAgentResponse,
  CreateApiTokenResponse,
  CreateUserResponse,
  ErrorResponse,
  Policy,
  PolicyPropagationResponse,
  RawEvent,
  SimulatePolicyResponse,
  TeamMember,
  TokenPairResponse,
  TraceGraph,
  TraceSummary,
  UserRole,
} from "./types";

// vite.config.ts proxies these to the interceptor (4001) / aggregator
// (4002) in dev; in production both would sit behind the same reverse
// proxy path prefixes.
const INTERCEPTOR_BASE = "/api/interceptor";
const AGGREGATOR_BASE = "/api/aggregator";

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
      body?.error.code ?? "UNKNOWN_ERROR",
      body?.error.message ?? response.statusText,
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

  listTraces: () => request<TraceSummary[]>(AGGREGATOR_BASE, "/traces"),
  getTrace: (traceId: string) => request<TraceGraph>(AGGREGATOR_BASE, `/traces/${traceId}`),
  getTraceEvents: (traceId: string) =>
    request<RawEvent[]>(AGGREGATOR_BASE, `/traces/${traceId}/events`),
};

export function liveWebSocketUrl(agentId: string): string {
  const { accessToken } = useAuthStore.getState();
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/live/${agentId}?token=${encodeURIComponent(accessToken ?? "")}`;
}
