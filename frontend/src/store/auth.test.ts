import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const STORAGE_KEY = "bastion.auth";

// A valid-shaped (unsigned, since this store only ever base64-decodes for
// display — see auth.ts's own comment) JWT payload.
function fakeAccessToken(claims: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none" }));
  const payload = btoa(JSON.stringify(claims));
  return `${header}.${payload}.`;
}

beforeEach(() => {
  localStorage.clear();
  vi.resetModules();
});

afterEach(() => {
  localStorage.clear();
});

describe("useAuthStore", () => {
  it("starts logged out when localStorage has nothing persisted", async () => {
    const { useAuthStore } = await import("./auth");
    const state = useAuthStore.getState();
    expect(state.accessToken).toBeNull();
    expect(state.role).toBeNull();
    expect(state.userId).toBeNull();
  });

  it("rehydrates from localStorage on module init, including decoded JWT claims", async () => {
    const token = fakeAccessToken({ sub: "user-1", org_id: "org-1", role: "owner", exp: 9999999999 });
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ accessToken: token, refreshToken: "refresh-1", role: "owner" }),
    );

    const { useAuthStore } = await import("./auth");
    const state = useAuthStore.getState();
    expect(state.accessToken).toBe(token);
    expect(state.userId).toBe("user-1");
    expect(state.orgId).toBe("org-1");
    expect(state.role).toBe("owner");
  });

  it("corrupted persisted JSON degrades to logged-out, not a thrown error", async () => {
    localStorage.setItem(STORAGE_KEY, "{not valid json");
    const { useAuthStore } = await import("./auth");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("setTokens updates state, decodes claims, and persists to localStorage", async () => {
    const { useAuthStore } = await import("./auth");
    const token = fakeAccessToken({ sub: "user-2", org_id: "org-2", role: "viewer", exp: 1 });

    useAuthStore
      .getState()
      .setTokens({ access_token: token, refresh_token: "refresh-2", role: "viewer" });

    const state = useAuthStore.getState();
    expect(state.accessToken).toBe(token);
    expect(state.userId).toBe("user-2");
    expect(state.orgId).toBe("org-2");
    expect(state.role).toBe("viewer");

    const persisted = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    expect(persisted.accessToken).toBe(token);
    expect(persisted.refreshToken).toBe("refresh-2");
  });

  it("logout clears both state and localStorage", async () => {
    const { useAuthStore } = await import("./auth");
    const token = fakeAccessToken({ sub: "user-3", org_id: "org-3", role: "admin", exp: 1 });
    useAuthStore
      .getState()
      .setTokens({ access_token: token, refresh_token: "refresh-3", role: "admin" });

    useAuthStore.getState().logout();

    const state = useAuthStore.getState();
    expect(state.accessToken).toBeNull();
    expect(state.refreshToken).toBeNull();
    expect(state.role).toBeNull();
    expect(state.userId).toBeNull();
    expect(state.orgId).toBeNull();
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("a malformed access token (not 3 base64 segments) decodes to null claims, not a crash", async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ accessToken: "not-a-jwt", refreshToken: "r", role: "owner" }),
    );
    const { useAuthStore } = await import("./auth");
    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("not-a-jwt");
    expect(state.userId).toBeNull();
    expect(state.orgId).toBeNull();
  });
});
