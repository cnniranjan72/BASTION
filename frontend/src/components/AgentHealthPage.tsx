import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { TableSkeleton } from "./TableSkeleton";
import { TopBar } from "./TopBar";
import type { AgentHealth } from "../api/types";

function scoreTone(score: number): "good" | "warning" | "danger" {
  if (score >= 80) return "good";
  if (score >= 50) return "warning";
  return "danger";
}

// U16 (v2 upgrade), FRONTEND_V2.md's Agent Health. health_score and its
// inputs (reliability/policy_compliance/tool_error_rate/approval_rate) are
// all real GET /agents/{id}/health aggregates -- the exact formula and why
// those weights: docs/adr/ADR-021.
export function AgentHealthPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const [health, setHealth] = useState<AgentHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!agentId) return;
    setLoading(true);
    api
      .getAgentHealth(agentId)
      .then(setHealth)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load agent health");
      })
      .finally(() => setLoading(false));
  }, [agentId]);

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page page--wide">
        <div className="page__header">
          <h1>{health ? health.agent_name : "Agent health"}</h1>
          <p className="page__subtitle">
            <Link to="/agents">← All agents</Link> · real call volume, cost, and a composite
            health score computed from this agent's own recorded events.
          </p>
        </div>

        {error && <p className="page__error">{error}</p>}

        {loading || !health ? (
          <TableSkeleton />
        ) : (
          <>
            <div className="stat-grid">
              <div className={`stat-card stat-card--score-${scoreTone(health.health_score)}`}>
                <span className="stat-card__value">{health.health_score.toFixed(0)}</span>
                <span className="stat-card__label">Health score / 100</span>
              </div>
              <div className="stat-card">
                <span className="stat-card__value">{health.calls_total}</span>
                <span className="stat-card__label">Calls ({health.window_days}d)</span>
              </div>
              <div className="stat-card stat-card--danger">
                <span className="stat-card__value">{health.blocked_total}</span>
                <span className="stat-card__label">Blocked</span>
              </div>
              <div className="stat-card">
                <span className="stat-card__value">{health.failed_total}</span>
                <span className="stat-card__label">Failed</span>
              </div>
              <div className="stat-card">
                <span className="stat-card__value">
                  {health.avg_latency_ms !== null ? `${health.avg_latency_ms.toFixed(0)}ms` : "—"}
                </span>
                <span className="stat-card__label">Avg latency</span>
              </div>
              <div className="stat-card">
                <span className="stat-card__value">${health.estimated_cost_total.toFixed(4)}</span>
                <span className="stat-card__label">Estimated cost</span>
              </div>
            </div>

            {health.anomalies.length > 0 && (
              <section className="threat-timeline">
                <h2>Anomalies</h2>
                <ul className="trace-list">
                  {health.anomalies.map((a) => (
                    <li key={a.description}>
                      <div className="trace-list__item trace-list__item--had_blocks">
                        <span className="trace-list__status">Anomaly</span>
                        <span className="trace-list__meta">{a.description}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section className="overview-recent">
              <h2>Score components</h2>
              <div className="table-scroll">
                <table className="data-table">
                  <tbody>
                    <tr>
                      <td>Reliability (completed vs. failed)</td>
                      <td>{(health.reliability * 100).toFixed(1)}%</td>
                    </tr>
                    <tr>
                      <td>Policy compliance (not blocked)</td>
                      <td>{(health.policy_compliance * 100).toFixed(1)}%</td>
                    </tr>
                    <tr>
                      <td>Tool error rate</td>
                      <td>{(health.tool_error_rate * 100).toFixed(1)}%</td>
                    </tr>
                    <tr>
                      <td>Approval rate (informational, not scored)</td>
                      <td>{(health.approval_rate * 100).toFixed(1)}%</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>

            <section className="overview-recent">
              <h2>Top tools by volume</h2>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Tool</th>
                      <th>Calls</th>
                    </tr>
                  </thead>
                  <tbody>
                    {health.top_tools.map((t) => (
                      <tr key={t.tool_name}>
                        <td className="data-table__mono">{t.tool_name}</td>
                        <td>{t.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
