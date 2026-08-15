import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let mockAccessToken: string | null = "old-token";
let mockRefreshToken: string | null = "refresh-token";
const setTokensMock = vi.fn(
  (tokens: { access_token: string; refresh_token: string; role: string }) => {
    mockAccessToken = tokens.access_token;
    mockRefreshToken = tokens.refresh_token;
  },
);
const logoutMock = vi.fn(() => {
  mockAccessToken = null;
  mockRefreshToken = null;
});

vi.mock("../store/auth", () => ({
  useAuthStore: {
    getState: () => ({
      accessToken: mockAccessToken,
      refreshToken: mockRefreshToken,
      setTokens: setTokensMock,
      logout: logoutMock,
    }),
  },
}));

const { api, ApiError } = await import("./client");

// client.ts's request() only ever touches .ok/.status/.statusText/.json() —
// a plain object avoids depending on whether the test environment's fetch/
// Response globals are actually present (jsdom doesn't implement fetch).
function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: `status ${status}`,
    json: async () => body,
  } as Response;
}

beforeEach(() => {
  mockAccessToken = "old-token";
  mockRefreshToken = "refresh-token";
  setTokensMock.mockClear();
  logoutMock.mockClear();
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function fetchMock() {
  return fetch as unknown as ReturnType<typeof vi.fn>;
}

describe("api client — happy path", () => {
  it("attaches the bearer token and parses a JSON body", async () => {
    fetchMock().mockResolvedValueOnce(mockResponse([{ id: "agent-1" }]));

    const result = await api.listAgents();

    expect(result).toEqual([{ id: "agent-1" }]);
    const [url, init] = fetchMock().mock.calls[0]!;
    expect(url).toContain("/agents");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer old-token");
  });

  it("a 204 response resolves to undefined instead of trying to parse a body", async () => {
    fetchMock().mockResolvedValueOnce(mockResponse(null, 204));
    const result = await api.revokeApiToken("token-1");
    expect(result).toBeUndefined();
  });
});

describe("api client — error mapping", () => {
  it("a JSON error body becomes an ApiError with its code/message", async () => {
    fetchMock().mockResolvedValueOnce(
      mockResponse(
        { error: { code: "AGENT_NOT_FOUND", message: "no such agent", request_id: "r-1" } },
        404,
      ),
    );

    await expect(api.listAgents()).rejects.toMatchObject({
      status: 404,
      code: "AGENT_NOT_FOUND",
      message: "no such agent",
    });
  });

  it("a non-JSON error body falls back to statusText and UNKNOWN_ERROR", async () => {
    fetchMock().mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: async () => {
        throw new SyntaxError("not json");
      },
    } as unknown as Response);

    await expect(api.listAgents()).rejects.toMatchObject({
      status: 500,
      code: "UNKNOWN_ERROR",
      message: "Internal Server Error",
    });
  });

  it("a valid JSON error body missing the `error` wrapper falls back cleanly, not a TypeError", async () => {
    // Real bug found writing this suite: `body?.error.code` (no chaining
    // past `.error`) threw a TypeError for any well-formed JSON body that
    // isn't shaped like BASTION's own error envelope — the try/catch above
    // only guards JSON *parse* failures, not "parsed fine, wrong shape."
    // Fixed to `body?.error?.code`.
    fetchMock().mockResolvedValueOnce(mockResponse({}, 503));

    await expect(api.listAgents()).rejects.toMatchObject({
      status: 503,
      code: "UNKNOWN_ERROR",
    });
  });

  it("ApiError instances are real Error subclasses (instanceof works)", async () => {
    fetchMock().mockResolvedValueOnce(
      mockResponse({ error: { code: "X", message: "y", request_id: "r" } }, 400),
    );
    try {
      await api.listAgents();
      throw new Error("expected api.listAgents() to reject");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
    }
  });
});

describe("api client — refresh-on-401", () => {
  it("a 401 triggers /auth/refresh, then retries the original request with the new token", async () => {
    fetchMock().mockImplementation((url: string) => {
      if (String(url).includes("/auth/refresh")) {
        return Promise.resolve(
          mockResponse({
            access_token: "new-token",
            refresh_token: "new-refresh",
            token_type: "bearer",
            role: "owner",
          }),
        );
      }
      // First call to /agents fails with 401; the retry (after refresh) succeeds.
      const alreadyRefreshed = setTokensMock.mock.calls.length > 0;
      return Promise.resolve(
        alreadyRefreshed ? mockResponse([{ id: "agent-1" }]) : mockResponse({}, 401),
      );
    });

    const result = await api.listAgents();

    expect(result).toEqual([{ id: "agent-1" }]);
    expect(setTokensMock).toHaveBeenCalledTimes(1);
    const refreshCalls = fetchMock().mock.calls.filter(([url]) =>
      String(url).includes("/auth/refresh"),
    );
    expect(refreshCalls).toHaveLength(1);
  });

  it("two concurrent 401s share exactly one /auth/refresh call, not two", async () => {
    let agentsCallCount = 0;
    fetchMock().mockImplementation((url: string) => {
      if (String(url).includes("/auth/refresh")) {
        return Promise.resolve(
          mockResponse({
            access_token: "new-token",
            refresh_token: "new-refresh",
            token_type: "bearer",
            role: "owner",
          }),
        );
      }
      agentsCallCount += 1;
      // The first two calls (both concurrent initial attempts) 401; every
      // call after the refresh succeeds.
      return Promise.resolve(agentsCallCount <= 2 ? mockResponse({}, 401) : mockResponse([]));
    });

    await Promise.all([api.listAgents(), api.listAgents()]);

    const refreshCalls = fetchMock().mock.calls.filter(([url]) =>
      String(url).includes("/auth/refresh"),
    );
    expect(refreshCalls).toHaveLength(1);
  });

  it("a failed refresh logs out and the original 401 propagates as an ApiError", async () => {
    fetchMock().mockImplementation((url: string) => {
      if (String(url).includes("/auth/refresh")) {
        return Promise.resolve(mockResponse({}, 401));
      }
      return Promise.resolve(mockResponse({}, 401));
    });

    await expect(api.listAgents()).rejects.toMatchObject({ status: 401 });
    expect(logoutMock).toHaveBeenCalledTimes(1);
    // Only the one original attempt — a failed refresh must not retry.
    const agentsCalls = fetchMock().mock.calls.filter(
      ([url]) => !String(url).includes("/auth/refresh"),
    );
    expect(agentsCalls).toHaveLength(1);
  });

  it("a 401 with no refresh token available does not attempt to refresh at all", async () => {
    mockRefreshToken = null;
    fetchMock().mockResolvedValue(mockResponse({}, 401));

    await expect(api.listAgents()).rejects.toMatchObject({ status: 401 });
    const refreshCalls = fetchMock().mock.calls.filter(([url]) =>
      String(url).includes("/auth/refresh"),
    );
    expect(refreshCalls).toHaveLength(0);
  });
});
