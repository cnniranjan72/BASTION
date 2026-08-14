import { create } from "zustand";
import type { UserRole } from "../api/types";

interface JwtClaims {
  sub: string; // user_id
  org_id: string;
  role: UserRole;
  exp: number;
}

// Decoded for *display* only (whose org am I in, what's my role) — never
// trusted for authorization, which always happens server-side against the
// signature. No verification here, just base64 decoding of the payload.
function decodeClaims(accessToken: string): JwtClaims | null {
  try {
    const payload = accessToken.split(".")[1];
    if (!payload) return null;
    return JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as JwtClaims;
  } catch {
    return null;
  }
}

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  role: UserRole | null;
  userId: string | null;
  orgId: string | null;
  setTokens: (tokens: { access_token: string; refresh_token: string; role: UserRole }) => void;
  logout: () => void;
}

const STORAGE_KEY = "bastion.auth";

function loadPersisted(): Pick<AuthState, "accessToken" | "refreshToken" | "role"> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { accessToken: null, refreshToken: null, role: null };
    return JSON.parse(raw) as Pick<AuthState, "accessToken" | "refreshToken" | "role">;
  } catch {
    return { accessToken: null, refreshToken: null, role: null };
  }
}

export const useAuthStore = create<AuthState>((set) => {
  const persisted = loadPersisted();
  const claims = persisted.accessToken ? decodeClaims(persisted.accessToken) : null;

  return {
    accessToken: persisted.accessToken,
    refreshToken: persisted.refreshToken,
    role: persisted.role,
    userId: claims?.sub ?? null,
    orgId: claims?.org_id ?? null,

    setTokens: (tokens) => {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          accessToken: tokens.access_token,
          refreshToken: tokens.refresh_token,
          role: tokens.role,
        }),
      );
      const claims = decodeClaims(tokens.access_token);
      set({
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
        role: tokens.role,
        userId: claims?.sub ?? null,
        orgId: claims?.org_id ?? null,
      });
    },

    logout: () => {
      localStorage.removeItem(STORAGE_KEY);
      set({ accessToken: null, refreshToken: null, role: null, userId: null, orgId: null });
    },
  };
});
