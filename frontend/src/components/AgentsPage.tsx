import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "../api/client";
import { useAuthStore } from "../store/auth";
import { toast } from "../store/toast";
import { EmptyState } from "./EmptyState";
import { AgentsIcon } from "./icons";
import { TableSkeleton } from "./TableSkeleton";
import { TopBar } from "./TopBar";
import type { Agent, Policy } from "../api/types";

function canManage(role: string | null): boolean {
  return role === "owner" || role === "admin";
}

export function AgentsPage() {
  const role = useAuthStore((s) => s.role);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [policySetId, setPolicySetId] = useState("");
  const [creating, setCreating] = useState(false);
  const [newKey, setNewKey] = useState<{ name: string; api_key: string } | null>(null);
  const [copied, setCopied] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [agentList, policyList] = await Promise.all([api.listAgents(), api.listPolicies()]);
      setAgents(agentList);
      setPolicies(policyList);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // One entry per policy_set_id (the stable identity across versions,
  // ARCHITECTURE.md §10) using whichever version happens to be active, so
  // the picker shows current policy names, not every historical version.
  const activePolicySets = Array.from(
    new Map(policies.filter((p) => p.active).map((p) => [p.policy_set_id, p])).values(),
  );

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setCreating(true);
    try {
      const created = await api.createAgent(name, policySetId || null);
      setNewKey({ name: created.name, api_key: created.api_key });
      setCopied(false);
      setName("");
      setPolicySetId("");
      toast.success(`Agent "${created.name}" created`);
      await load();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to create agent";
      setError(message);
      toast.error(message);
    } finally {
      setCreating(false);
    }
  }

  async function handleReassign(agentId: string, newPolicySetId: string) {
    setError(null);
    try {
      await api.updateAgentPolicySet(agentId, newPolicySetId || null);
      toast.success("Policy updated");
      await load();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to update agent";
      setError(message);
      toast.error(message);
    }
  }

  function policyName(policySetId: string | null): string {
    if (!policySetId) return "none — all calls allowed";
    const match = activePolicySets.find((p) => p.policy_set_id === policySetId);
    return match ? match.name : policySetId.slice(0, 8);
  }

  async function copyKey() {
    if (!newKey) return;
    await navigator.clipboard.writeText(newKey.api_key);
    setCopied(true);
  }

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page">
        <div className="page__header">
          <h1>Agents</h1>
          <p className="page__subtitle">
            Each agent authenticates with its own API key and is optionally bound to a policy.
          </p>
        </div>

        {error && <p className="page__error">{error}</p>}

        {newKey && (
          <div className="key-reveal">
            <p>
              <strong>{newKey.name}</strong> created. Copy its API key now — it won't be shown
              again.
            </p>
            <div className="key-reveal__row">
              <code>{newKey.api_key}</code>
              <button onClick={copyKey}>{copied ? "Copied" : "Copy"}</button>
            </div>
            <button className="key-reveal__dismiss" onClick={() => setNewKey(null)}>
              Done
            </button>
          </div>
        )}

        {canManage(role) && (
          <form className="inline-form" onSubmit={handleCreate}>
            <input
              placeholder="Agent name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <select value={policySetId} onChange={(e) => setPolicySetId(e.target.value)}>
              <option value="">No policy (allow all)</option>
              {activePolicySets.map((p) => (
                <option key={p.policy_set_id} value={p.policy_set_id}>
                  {p.name}
                </option>
              ))}
            </select>
            <button type="submit" disabled={creating}>
              {creating ? "Creating…" : "Create agent"}
            </button>
          </form>
        )}

        {loading ? (
          <TableSkeleton />
        ) : agents.length === 0 ? (
          <EmptyState icon={AgentsIcon} title="No agents yet">
            {canManage(role)
              ? "Create one above to get an API key — that's what your agent authenticates with."
              : "Ask an owner or admin to create one."}
          </EmptyState>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Policy</th>
                  <th>Agent ID</th>
                  <th>Created</th>
                  {canManage(role) && <th>Reassign policy</th>}
                </tr>
              </thead>
              <tbody>
                {agents.map((agent) => (
                  <tr key={agent.id}>
                    <td>{agent.name}</td>
                    <td>{policyName(agent.policy_set_id)}</td>
                    <td className="data-table__mono">{agent.id}</td>
                    <td>{new Date(agent.created_at).toLocaleString()}</td>
                    {canManage(role) && (
                      <td>
                        <select
                          value={agent.policy_set_id ?? ""}
                          onChange={(e) => handleReassign(agent.id, e.target.value)}
                        >
                          <option value="">No policy (allow all)</option>
                          {activePolicySets.map((p) => (
                            <option key={p.policy_set_id} value={p.policy_set_id}>
                              {p.name}
                            </option>
                          ))}
                        </select>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
