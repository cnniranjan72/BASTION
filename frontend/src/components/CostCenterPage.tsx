import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { EmptyState } from "./EmptyState";
import { CostIcon } from "./icons";
import { TableSkeleton } from "./TableSkeleton";
import { TopBar } from "./TopBar";
import type { CostSummary } from "../api/types";

// U16 (v2 upgrade), FRONTEND_V2.md's Cost Center. total_cost/by_agent/
// by_tool are real, from events.payload->>'cost'; estimated_savings is a
// real estimate (this org's own avg cost per agent+tool × its blocked-call
// count for that pair) -- a blocked call never runs, so never has a real
// recorded cost. docs/adr/ADR-021 has the exact methodology.
export function CostCenterPage() {
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [windowDays, setWindowDays] = useState(30);

  useEffect(() => {
    setLoading(true);
    api
      .getCosts(windowDays)
      .then(setSummary)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load cost data");
      })
      .finally(() => setLoading(false));
  }, [windowDays]);

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page page--wide">
        <div className="page__header">
          <h1>Cost Center</h1>
          <p className="page__subtitle">
            Real spend by agent and tool, computed from recorded call cost — plus what policy
            enforcement is estimated to have saved by stopping calls before they ran.
          </p>
        </div>

        {error && <p className="page__error">{error}</p>}

        <div className="filter-bar">
          <select value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))}>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
        </div>

        {loading ? (
          <TableSkeleton />
        ) : !summary || summary.total_cost === 0 ? (
          <EmptyState icon={CostIcon} title="No recorded cost in this window">
            No completed call in this window reported a cost — the SDK's caller has to opt into
            reporting `cost` on `POST /spans/{"{"}id{"}"}/complete` for this to have data.
          </EmptyState>
        ) : (
          <>
            <div className="stat-grid">
              <div className="stat-card">
                <CostIcon className="stat-card__icon" width={20} height={20} />
                <span className="stat-card__value">${summary.total_cost.toFixed(4)}</span>
                <span className="stat-card__label">Total spend ({windowDays}d)</span>
              </div>
              <div className="stat-card">
                <span className="stat-card__value">
                  ${summary.estimated_savings_from_policy_enforcement.toFixed(4)}
                </span>
                <span className="stat-card__label">Est. saved by policy enforcement</span>
              </div>
            </div>

            <section className="overview-recent">
              <h2>Spend by agent</h2>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Agent</th>
                      <th>Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.by_agent.map((a) => (
                      <tr key={a.agent_id}>
                        <td>{a.agent_name}</td>
                        <td>${a.cost.toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="overview-recent">
              <h2>Spend by tool</h2>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Tool</th>
                      <th>Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.by_tool.map((t) => (
                      <tr key={t.tool_name}>
                        <td className="data-table__mono">{t.tool_name}</td>
                        <td>${t.cost.toFixed(4)}</td>
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
