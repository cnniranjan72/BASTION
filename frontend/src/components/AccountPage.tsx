import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "../api/client";
import { useAuthStore } from "../store/auth";
import { toast } from "../store/toast";
import { EmptyState } from "./EmptyState";
import { KeyIcon } from "./icons";
import { TableSkeleton } from "./TableSkeleton";
import { TopBar } from "./TopBar";
import type { ApiToken, TeamMember } from "../api/types";

export function AccountPage() {
  const { role, orgId, userId } = useAuthStore();
  const [profile, setProfile] = useState<TeamMember | null>(null);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);

  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [tokensLoading, setTokensLoading] = useState(true);
  const [tokenName, setTokenName] = useState("");
  const [creatingToken, setCreatingToken] = useState(false);
  const [reveal, setReveal] = useState<{ name: string; token: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);

  useEffect(() => {
    // No GET /users/me endpoint exists — the org member list already has
    // this user's row, and reusing it avoids adding a redundant endpoint.
    api
      .listUsers()
      .then((members) => setProfile(members.find((m) => m.id === userId) ?? null))
      .catch(() => {
        // Non-fatal — the page still works without the profile card.
      });
    loadTokens();
  }, [userId]);

  async function loadTokens() {
    setTokensLoading(true);
    try {
      setTokens(await api.listApiTokens());
    } catch {
      // Non-fatal for the rest of the page.
    } finally {
      setTokensLoading(false);
    }
  }

  async function handleChangePassword(event: FormEvent) {
    event.preventDefault();
    setPasswordError(null);
    if (newPassword !== confirmPassword) {
      setPasswordError("New password and confirmation don't match.");
      return;
    }
    setChangingPassword(true);
    try {
      await api.changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      toast.success("Password changed");
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to change password";
      setPasswordError(message);
      toast.error(message);
    } finally {
      setChangingPassword(false);
    }
  }

  async function handleCreateToken(event: FormEvent) {
    event.preventDefault();
    setCreatingToken(true);
    try {
      const created = await api.createApiToken(tokenName);
      setReveal({ name: created.name, token: created.token });
      setCopied(false);
      setTokenName("");
      toast.success(`Token "${created.name}" created`);
      await loadTokens();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to create token");
    } finally {
      setCreatingToken(false);
    }
  }

  async function handleRevoke(id: string) {
    setRevoking(id);
    try {
      await api.revokeApiToken(id);
      toast.success("Token revoked");
      await loadTokens();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to revoke token");
    } finally {
      setRevoking(null);
    }
  }

  async function copyToken() {
    if (!reveal) return;
    await navigator.clipboard.writeText(reveal.token);
    setCopied(true);
  }

  const activeTokens = tokens.filter((t) => !t.revoked_at);

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page">
        <div className="page__header">
          <h1>Account</h1>
          <p className="page__subtitle">Your profile, password, and API access.</p>
        </div>

        <section className="account-section">
          <h2>Profile</h2>
          <dl className="stat-list">
            <div>
              <dt>Email</dt>
              <dd>{profile?.email ?? "—"}</dd>
            </div>
            <div>
              <dt>Role</dt>
              <dd>{role}</dd>
            </div>
            <div>
              <dt>Organization</dt>
              <dd className="data-table__mono">{orgId}</dd>
            </div>
            {profile && (
              <div>
                <dt>Member since</dt>
                <dd>{new Date(profile.created_at).toLocaleDateString()}</dd>
              </div>
            )}
          </dl>
        </section>

        <section className="account-section">
          <h2>Change password</h2>
          {passwordError && <p className="page__error">{passwordError}</p>}
          <form className="inline-form inline-form--column" onSubmit={handleChangePassword}>
            <input
              type="password"
              placeholder="Current password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
            />
            <input
              type="password"
              placeholder="New password (min 8 characters)"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              minLength={8}
              required
            />
            <input
              type="password"
              placeholder="Confirm new password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              minLength={8}
              required
            />
            <button type="submit" disabled={changingPassword}>
              {changingPassword ? "Changing…" : "Change password"}
            </button>
          </form>
        </section>

        <section className="account-section">
          <h2>API tokens</h2>
          <p className="page__subtitle">
            For scripts, CI, or anything that needs to call this API without an interactive login.
            A token carries your own role and permissions — treat it like a password.
          </p>

          {reveal && (
            <div className="key-reveal">
              <p>
                <strong>{reveal.name}</strong> created. Copy it now — it won't be shown again.
              </p>
              <div className="key-reveal__row">
                <code>{reveal.token}</code>
                <button onClick={copyToken}>{copied ? "Copied" : "Copy"}</button>
              </div>
              <button className="key-reveal__dismiss" onClick={() => setReveal(null)}>
                Done
              </button>
            </div>
          )}

          <form className="inline-form" onSubmit={handleCreateToken}>
            <input
              placeholder="Token name, e.g. 'CI pipeline'"
              value={tokenName}
              onChange={(e) => setTokenName(e.target.value)}
              required
            />
            <button type="submit" disabled={creatingToken}>
              {creatingToken ? "Creating…" : "Create token"}
            </button>
          </form>

          {tokensLoading ? (
            <TableSkeleton />
          ) : activeTokens.length === 0 ? (
            <EmptyState icon={KeyIcon} title="No API tokens yet">
              Create one above to call BASTION's API from outside the dashboard.
            </EmptyState>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Prefix</th>
                    <th>Created</th>
                    <th>Last used</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {activeTokens.map((t) => (
                    <tr key={t.id}>
                      <td>{t.name}</td>
                      <td className="data-table__mono">{t.token_prefix}…</td>
                      <td>{new Date(t.created_at).toLocaleDateString()}</td>
                      <td>{t.last_used_at ? new Date(t.last_used_at).toLocaleString() : "Never"}</td>
                      <td>
                        <button onClick={() => handleRevoke(t.id)} disabled={revoking === t.id}>
                          {revoking === t.id ? "Revoking…" : "Revoke"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
