# BASTION — Auth Design

Two separate auth domains. Don't conflate them.

## 1. Agent-to-BASTION auth (machine auth)
- Each `Agent` gets an API key at registration: `BASTION_sk_<random 32 bytes>`.
- Store only the hash (SHA-256 is fine here — this is a lookup key, not a password, so no need for slow hashing).
- Sent as `Authorization: Bearer <key>` on every SDK call.
- Rotation: support issuing a second active key per agent so teams can rotate without downtime (old + new both valid during a grace window, then old is revoked). This mirrors real-world key rotation practice — document it even if you don't build a UI for it.

## 2. Human user auth (dashboard/approvals)
This is where you show real depth — do it properly, not decoratively.

### Login flow
1. Password login → argon2id verify.
2. On success, issue:
   - **Access token** (JWT, short-lived — 15 min, signed with an asymmetric key so the interceptor/aggregator services can verify it without calling the auth service)
   - **Refresh token** (opaque random string, long-lived — 7–30 days, stored server-side hashed)

### Refresh token rotation (the part interviewers actually probe on)
- Every time a refresh token is used, it is **invalidated** and a **new one issued** in its place — one-time-use tokens.
- All refresh tokens issued from the same login session share a `family_id`.
- **Reuse detection:** if a refresh token is presented that's already been used (i.e., it's not the current valid token for its family), treat this as a signal of token theft — **revoke the entire token family immediately**, forcing full re-login. This is the actual mechanism that makes rotation meaningful; without reuse detection, rotation alone doesn't stop a stolen token from being used in parallel with the legitimate one.
- Access tokens are stateless (JWT) — you don't revoke them individually; you rely on their short TTL. This tradeoff (can't instantly revoke a stolen access token, only wait out 15 min) is a real design decision — know it and be able to defend it, or add a denylist cache (Redis, short TTL) if you want instant revocation and are willing to pay the lookup cost per request.

### Authorization (RBAC)
- Roles: `owner` (billing, org settings), `admin` (manage agents/policies), `approver` (can resolve approval requests), `viewer` (read-only dashboard/traces).
- Enforce at the API layer via middleware that checks role against the requested action — never trust the frontend to hide a button as the only control.

### Session/device handling
- Refresh tokens are bound to a device/session identifier so a user can view "active sessions" and revoke one device without logging out everywhere — a good "production polish" feature to mention even if v1 only supports revoke-all.

## 3. Why this matters for the product itself
Auth isn't just "log in to see the dashboard" here — the *policy engine's* trust model depends on it. An `approver` role resolving a blocked payment call is itself a security-critical action; if that auth is weak, the whole guardrail promise of the product is undermined. This is a good point to make explicitly in an interview: the auth system isn't bolted on, it's part of the product's core value proposition.
