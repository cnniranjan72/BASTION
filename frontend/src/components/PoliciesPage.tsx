import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "../api/client";
import { useAuthStore } from "../store/auth";
import { TopBar } from "./TopBar";
import type { Policy } from "../api/types";

function canManage(role: string | null): boolean {
  return role === "owner" || role === "admin";
}

const EXAMPLE_DEFINITION = `[
  {
    "match": { "tool": "payments.transfer" },
    "action": "block",
    "condition": "amount > 100"
  },
  {
    "match": { "tool": "*" },
    "action": "allow"
  }
]`;

export function PoliciesPage() {
  const role = useAuthStore((s) => s.role);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activating, setActivating] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [definitionText, setDefinitionText] = useState(EXAMPLE_DEFINITION);
  const [creating, setCreating] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setPolicies(await api.listPolicies());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load policies");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // Group versions by policy_set_id (stable identity, §10), newest first.
  const sets = new Map<string, Policy[]>();
  for (const policy of policies) {
    const existing = sets.get(policy.policy_set_id) ?? [];
    existing.push(policy);
    sets.set(policy.policy_set_id, existing);
  }
  for (const versions of sets.values()) versions.sort((a, b) => b.version - a.version);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setError(null);
    let definition: Policy["definition"];
    try {
      definition = JSON.parse(definitionText);
    } catch {
      setError("Definition must be valid JSON — see the example format below the field.");
      return;
    }
    setCreating(true);
    try {
      await api.createPolicy(name, definition);
      setName("");
      setDefinitionText(EXAMPLE_DEFINITION);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create policy");
    } finally {
      setCreating(false);
    }
  }

  async function handleActivate(id: string) {
    setError(null);
    setActivating(id);
    try {
      await api.activatePolicy(id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to activate policy");
    } finally {
      setActivating(null);
    }
  }

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page">
        <div className="page__header">
          <h1>Policies</h1>
          <p className="page__subtitle">
            Versioned, never edited in place — activating a version hot-reloads every running
            interceptor with no restart.
          </p>
        </div>

        {error && <p className="page__error">{error}</p>}

        {canManage(role) && (
          <form className="inline-form inline-form--column" onSubmit={handleCreate}>
            <input
              placeholder="Policy name (reuse an existing name to add a new version)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <textarea
              rows={8}
              value={definitionText}
              onChange={(e) => setDefinitionText(e.target.value)}
              spellCheck={false}
            />
            <button type="submit" disabled={creating}>
              {creating ? "Creating…" : "Create version"}
            </button>
          </form>
        )}

        {loading ? (
          <p className="page__empty">Loading…</p>
        ) : sets.size === 0 ? (
          <p className="page__empty">No policies yet.</p>
        ) : (
          <div className="policy-sets">
            {Array.from(sets.entries()).map(([policySetId, versions]) => (
              <div key={policySetId} className="policy-set">
                <h2>{versions[0]?.name ?? "(unnamed)"}</h2>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Version</th>
                      <th>Status</th>
                      <th>Created</th>
                      <th>Definition</th>
                      {canManage(role) && <th />}
                    </tr>
                  </thead>
                  <tbody>
                    {versions.map((v) => (
                      <tr key={v.id}>
                        <td>v{v.version}</td>
                        <td>
                          {v.active ? (
                            <span className="badge badge--active">active</span>
                          ) : (
                            <span className="badge">inactive</span>
                          )}
                        </td>
                        <td>{new Date(v.created_at).toLocaleString()}</td>
                        <td>
                          <pre className="data-table__json">
                            {JSON.stringify(v.definition, null, 2)}
                          </pre>
                        </td>
                        {canManage(role) && (
                          <td>
                            {!v.active && (
                              <button
                                onClick={() => handleActivate(v.id)}
                                disabled={activating === v.id}
                              >
                                {activating === v.id ? "Activating…" : "Activate"}
                              </button>
                            )}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
