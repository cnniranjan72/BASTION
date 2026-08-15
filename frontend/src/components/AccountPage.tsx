import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useAuthStore } from "../store/auth";
import { toast } from "../store/toast";
import { EmptyState } from "./EmptyState";
import { KeyIcon } from "./icons";
import { TableSkeleton } from "./TableSkeleton";
import { TopBar } from "./TopBar";
import type { ApiToken, LiveDemoRunResponse, LlmCredential, LlmProvider, TeamMember } from "../api/types";

const LLM_PROVIDERS: LlmProvider[] = ["openai", "anthropic", "gemini"];

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

  const [llmCredentials, setLlmCredentials] = useState<LlmCredential[]>([]);
  const [llmCredentialsLoading, setLlmCredentialsLoading] = useState(true);
  const [llmProvider, setLlmProvider] = useState<LlmProvider>("openai");
  const [llmLabel, setLlmLabel] = useState("");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [creatingLlmCredential, setCreatingLlmCredential] = useState(false);
  const [revokingLlmCredential, setRevokingLlmCredential] = useState<string | null>(null);

  const [demoProvider, setDemoProvider] = useState<LlmProvider | "ollama">("ollama");
  const [demoCredentialId, setDemoCredentialId] = useState<string>("");
  const [runningDemo, setRunningDemo] = useState(false);
  const [demoResult, setDemoResult] = useState<LiveDemoRunResponse | null>(null);
  const [demoError, setDemoError] = useState<string | null>(null);

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
    loadLlmCredentials();
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

  async function loadLlmCredentials() {
    setLlmCredentialsLoading(true);
    try {
      setLlmCredentials(await api.listLlmCredentials());
    } catch {
      // Non-fatal for the rest of the page.
    } finally {
      setLlmCredentialsLoading(false);
    }
  }

  async function handleCreateLlmCredential(event: FormEvent) {
    event.preventDefault();
    setCreatingLlmCredential(true);
    try {
      await api.createLlmCredential(llmProvider, llmLabel, llmApiKey);
      setLlmLabel("");
      setLlmApiKey("");
      toast.success(`${llmProvider} key added`);
      await loadLlmCredentials();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to add key");
    } finally {
      setCreatingLlmCredential(false);
    }
  }

  async function handleRevokeLlmCredential(id: string) {
    setRevokingLlmCredential(id);
    try {
      await api.revokeLlmCredential(id);
      toast.success("Key revoked");
      await loadLlmCredentials();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to revoke key");
    } finally {
      setRevokingLlmCredential(null);
    }
  }

  async function handleRunLiveDemo() {
    setRunningDemo(true);
    setDemoError(null);
    setDemoResult(null);
    try {
      const result = await api.runLiveDemo(
        demoProvider,
        demoProvider === "ollama" ? null : demoCredentialId || null,
      );
      setDemoResult(result);
    } catch (err) {
      setDemoError(err instanceof ApiError ? err.message : "Failed to run live demo");
    } finally {
      setRunningDemo(false);
    }
  }

  const activeTokens = tokens.filter((t) => !t.revoked_at);
  const activeLlmCredentials = llmCredentials.filter((c) => !c.revoked_at);
  const credentialsForDemoProvider = activeLlmCredentials.filter(
    (c) => c.provider === demoProvider,
  );

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

        <section className="account-section">
          <h2>LLM provider keys</h2>
          <p className="page__subtitle">
            Bring your own OpenAI, Anthropic, or Gemini key to run the live prompt-injection demo
            below with a real model — the key is encrypted at rest and only decrypted for that one
            call. Prefer not to share a key? Use local Ollama instead, no key needed.
          </p>

          <form className="inline-form" onSubmit={handleCreateLlmCredential}>
            <select value={llmProvider} onChange={(e) => setLlmProvider(e.target.value as LlmProvider)}>
              {LLM_PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <input
              placeholder="Label, e.g. 'personal key'"
              value={llmLabel}
              onChange={(e) => setLlmLabel(e.target.value)}
              required
            />
            <input
              type="password"
              placeholder="API key"
              value={llmApiKey}
              onChange={(e) => setLlmApiKey(e.target.value)}
              required
            />
            <button type="submit" disabled={creatingLlmCredential}>
              {creatingLlmCredential ? "Adding…" : "Add key"}
            </button>
          </form>

          {llmCredentialsLoading ? (
            <TableSkeleton />
          ) : activeLlmCredentials.length === 0 ? (
            <EmptyState icon={KeyIcon} title="No LLM keys yet">
              Add one above, or skip this and run the demo with local Ollama.
            </EmptyState>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Provider</th>
                    <th>Label</th>
                    <th>Key</th>
                    <th>Created</th>
                    <th>Last used</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {activeLlmCredentials.map((c) => (
                    <tr key={c.id}>
                      <td>{c.provider}</td>
                      <td>{c.label}</td>
                      <td className="data-table__mono">…{c.key_last4}</td>
                      <td>{new Date(c.created_at).toLocaleDateString()}</td>
                      <td>{c.last_used_at ? new Date(c.last_used_at).toLocaleString() : "Never"}</td>
                      <td>
                        <button
                          onClick={() => handleRevokeLlmCredential(c.id)}
                          disabled={revokingLlmCredential === c.id}
                        >
                          {revokingLlmCredential === c.id ? "Revoking…" : "Revoke"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="account-section">
          <h2>Run the live prompt-injection demo</h2>
          <p className="page__subtitle">
            A real LLM reads a support ticket containing an injected instruction and decides which
            tool to call — every decision it makes still goes through BASTION's real policy engine.
            (The scheduled reliability demo elsewhere in this system uses a scripted stand-in
            instead, deliberately, so it isn't flaky — see ARCHITECTURE.md §17. This one is real.)
          </p>

          <div className="inline-form">
            <select
              value={demoProvider}
              onChange={(e) => {
                setDemoProvider(e.target.value as LlmProvider | "ollama");
                setDemoCredentialId("");
              }}
            >
              <option value="ollama">ollama (local)</option>
              {LLM_PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            {demoProvider !== "ollama" && (
              <select
                value={demoCredentialId}
                onChange={(e) => setDemoCredentialId(e.target.value)}
                required
              >
                <option value="" disabled>
                  Choose a {demoProvider} key…
                </option>
                {credentialsForDemoProvider.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.label} (…{c.key_last4})
                  </option>
                ))}
              </select>
            )}
            <button
              onClick={handleRunLiveDemo}
              disabled={
                runningDemo || (demoProvider !== "ollama" && credentialsForDemoProvider.length === 0)
              }
            >
              {runningDemo ? "Running…" : "Run live demo"}
            </button>
          </div>

          {demoProvider !== "ollama" && credentialsForDemoProvider.length === 0 && (
            <p className="page__subtitle">Add a {demoProvider} key above first.</p>
          )}

          {demoError && <p className="page__error">{demoError}</p>}

          {demoResult && (
            <div className="key-reveal">
              <ol>
                {demoResult.steps.map((step, i) => (
                  <li key={i}>
                    <code>{step.tool_name}</code>
                    {" — "}
                    <strong>{step.decision}</strong>
                    {step.reason && <> ({step.reason})</>}
                  </li>
                ))}
              </ol>
              {demoResult.final_text && <p>{demoResult.final_text}</p>}
              <Link to={`/replay/${demoResult.trace_id}`}>View this trace in Incident Replay →</Link>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
