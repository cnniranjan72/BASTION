import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../../api/client";
import { toast } from "../../store/toast";
import { TopBar } from "../TopBar";
import { RuleBuilder } from "./RuleBuilder";
import { PolicySimulator } from "./PolicySimulator";
import { VersionDiff } from "./VersionDiff";
import { PropagationStatus } from "./PropagationStatus";
import type { Agent, Policy, PolicyRule } from "../../api/types";

const NEW_SET = "__new__";
const DEFAULT_RULES: PolicyRule[] = [{ match: { tool: "*" }, action: "allow" }];

/** U15 (v2 upgrade), Policy Studio — FRONTEND_V2.md flagship #2. A
 * structured visual builder over the real DSL (not a JSON textarea, that's
 * PoliciesPage's job and stays as the simple fallback view), the real
 * simulator (POST /policies/simulate), a real version diff, and real
 * propagation status — see docs/adr/ADR-020 for the two backend
 * endpoints this phase added to make the last two possible without
 * faking anything. */
export function PolicyStudioPage() {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedSetId, setSelectedSetId] = useState<string>(NEW_SET);
  const [name, setName] = useState("");
  const [rules, setRules] = useState<PolicyRule[]>(DEFAULT_RULES);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [p, a] = await Promise.all([api.listPolicies(), api.listAgents()]);
      setPolicies(p);
      setAgents(a);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const sets = useMemo(() => {
    const map = new Map<string, Policy[]>();
    for (const p of policies) {
      const existing = map.get(p.policy_set_id) ?? [];
      existing.push(p);
      map.set(p.policy_set_id, existing);
    }
    for (const versions of map.values()) versions.sort((a, b) => b.version - a.version);
    return map;
  }, [policies]);

  const selectedVersions = selectedSetId !== NEW_SET ? (sets.get(selectedSetId) ?? []) : [];
  const latest = selectedVersions[0] ?? null;
  const active = selectedVersions.find((v) => v.active) ?? null;

  function selectSet(setId: string) {
    setSelectedSetId(setId);
    if (setId === NEW_SET) {
      setName("");
      setRules(DEFAULT_RULES);
      return;
    }
    const versions = sets.get(setId) ?? [];
    const top = versions[0];
    if (top) {
      setName(top.name);
      setRules(top.definition);
    }
  }

  async function handleSave() {
    if (!name.trim()) {
      toast.error("Name the policy first.");
      return;
    }
    setSaving(true);
    try {
      const created = await api.createPolicy(name.trim(), rules, latest?.version);
      toast.success(`"${created.name}" v${created.version} created — activate it to go live.`);
      await load();
      setSelectedSetId(created.policy_set_id);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save policy");
    } finally {
      setSaving(false);
    }
  }

  async function handleActivate(id: string) {
    try {
      const activated = await api.activatePolicy(id);
      toast.success(`v${activated.version} is now active`);
      await load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to activate");
    }
  }

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page policy-studio">
        <div className="page__header">
          <h1>Policy Studio</h1>
          <p className="page__subtitle">
            A visual builder over the real policy DSL — compiles to the same shape POST /policies
            consumes.
          </p>
        </div>

        {error && <p className="page__error">{error}</p>}
        {loading ? (
          <p>Loading…</p>
        ) : (
          <>
            <section className="policy-studio__section">
              <h2>Builder</h2>
              <div className="policy-studio__set-picker">
                <select value={selectedSetId} onChange={(e) => selectSet(e.target.value)}>
                  <option value={NEW_SET}>+ New policy</option>
                  {Array.from(sets.entries()).map(([setId, versions]) => (
                    <option key={setId} value={setId}>
                      {versions[0]?.name} {versions.some((v) => v.active) ? "(active)" : ""}
                    </option>
                  ))}
                </select>
                <input
                  placeholder="Policy name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>

              <RuleBuilder rules={rules} onChange={setRules} />

              <div className="policy-studio__save-row">
                <button onClick={handleSave} disabled={saving}>
                  {saving ? "Saving…" : latest ? `Save as v${latest.version + 1}` : "Save as v1"}
                </button>
                {latest && !latest.active && (
                  <button onClick={() => handleActivate(latest.id)} className="button--secondary">
                    Activate v{latest.version}
                  </button>
                )}
                {active && <PropagationStatus policySetId={active.policy_set_id} />}
              </div>
            </section>

            {selectedVersions.length >= 2 && (
              <section className="policy-studio__section">
                <h2>Version diff</h2>
                <VersionDiff versions={selectedVersions} agents={agents} />
              </section>
            )}

            <section className="policy-studio__section">
              <h2>Simulator</h2>
              <PolicySimulator agents={agents} />
            </section>
          </>
        )}
      </div>
    </div>
  );
}
