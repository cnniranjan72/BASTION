import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useAuthStore } from "../store/auth";
import { useCountUp } from "../hooks/useCountUp";
import { TopBar } from "./TopBar";
import type { Agent, ApprovalRequest, TraceSummary } from "../api/types";

interface Stats {
  agents: Agent[];
  activePolicySets: number;
  approvals: ApprovalRequest[];
  traces: TraceSummary[];
}

function StatCard({
  to,
  label,
  value,
  tone,
}: {
  to: string;
  label: string;
  value: string | number;
  tone?: "danger" | "warning";
}) {
  const numeric = typeof value === "number" ? value : null;
  const animated = useCountUp(numeric ?? 0);
  const display = numeric === null ? value : animated;
  return (
    <Link to={to} className={`stat-card${tone ? ` stat-card--${tone}` : ""}`}>
      <span className="stat-card__value">{display}</span>
      <span className="stat-card__label">{label}</span>
    </Link>
  );
}

export function OverviewPage() {
  const role = useAuthStore((s) => s.role);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.listAgents(), api.listPolicies(), api.listApprovals(), api.listTraces()])
      .then(([agents, policies, approvals, traces]) => {
        const activePolicySets = new Set(policies.filter((p) => p.active).map((p) => p.policy_set_id))
          .size;
        setStats({ agents, activePolicySets, approvals, traces });
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load overview");
      });
  }, []);

  const totalCalls = stats?.traces.reduce((sum, t) => sum + t.total_calls, 0) ?? 0;
  const blockedCalls = stats?.traces.reduce((sum, t) => sum + t.blocked_calls, 0) ?? 0;
  const totalCost = stats?.traces.reduce((sum, t) => sum + t.total_cost, 0) ?? 0;

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page page--wide">
        <div className="page__header">
          <h1>Overview</h1>
          <p className="page__subtitle">Everything BASTION has seen across your organization.</p>
        </div>

        {error && <p className="page__error">{error}</p>}

        {stats && stats.agents.length === 0 ? (
          <div className="onboarding">
            <h2>Get started</h2>
            <ol className="onboarding__steps">
              <li>
                <Link to="/agents">Create an agent</Link> — it gets its own API key. Route your
                agent's tool calls through the BASTION SDK using that key.
              </li>
              <li>
                <Link to="/policies">Write a policy</Link> — declare what your agent may do
                unattended, what needs a human, and what's blocked outright.
              </li>
              <li>
                <Link to="/">Watch it live</Link> — connect the graph view to your agent and see
                every call, decision, and block as it happens.
              </li>
            </ol>
          </div>
        ) : (
          <>
            <div className="stat-grid">
              <StatCard to="/agents" label="Agents" value={stats?.agents.length ?? "—"} />
              <StatCard
                to="/policies"
                label="Active policies"
                value={stats?.activePolicySets ?? "—"}
              />
              <StatCard
                to="/approvals"
                label="Pending approvals"
                value={stats?.approvals.length ?? "—"}
                tone={stats && stats.approvals.length > 0 ? "warning" : undefined}
              />
              <StatCard
                to="/graph"
                label="Blocked calls"
                value={blockedCalls}
                tone={blockedCalls > 0 ? "danger" : undefined}
              />
              <StatCard to="/graph" label="Total calls" value={totalCalls} />
              <StatCard to="/graph" label="Total cost" value={`$${totalCost.toFixed(4)}`} />
            </div>

            <section className="overview-recent">
              <h2>Recent traces</h2>
              {stats && stats.traces.length === 0 ? (
                <p className="page__empty">No completed traces yet.</p>
              ) : (
                <ul className="trace-list">
                  {stats?.traces.slice(0, 6).map((t) => (
                    <li key={t.trace_id}>
                      <Link
                        to="/graph"
                        className={`trace-list__item trace-list__item--${t.status}`}
                      >
                        <span className="trace-list__status">{t.status}</span>
                        <span className="trace-list__meta">
                          {t.total_calls} calls · {t.blocked_calls} blocked ·{" "}
                          {new Date(t.started_at).toLocaleString()}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}

        {role && (
          <p className="page__subtitle" style={{ marginTop: "2rem" }}>
            Signed in as <strong>{role}</strong>. <Link to="/team">Manage your team →</Link>
          </p>
        )}
      </div>
    </div>
  );
}
