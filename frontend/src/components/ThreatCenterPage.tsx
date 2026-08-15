import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { EmptyState } from "./EmptyState";
import { ThreatIcon } from "./icons";
import { TableSkeleton } from "./TableSkeleton";
import { TopBar } from "./TopBar";
import type { ThreatSummary } from "../api/types";

// U16 (v2 upgrade), FRONTEND_V2.md's Threat Center. Every number here is a
// real GET /threats aggregate over blocked calls — see docs/adr/ADR-021 for
// why "threats" means blocked calls specifically (no separate
// prompt-injection detector exists in this codebase).
export function ThreatCenterPage() {
  const [summary, setSummary] = useState<ThreatSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [windowDays, setWindowDays] = useState(30);

  useEffect(() => {
    setLoading(true);
    api
      .getThreats(windowDays)
      .then(setSummary)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load threat data");
      })
      .finally(() => setLoading(false));
  }, [windowDays]);

  const maxBlocked = summary ? Math.max(1, ...summary.timeline.map((b) => b.blocked_count)) : 1;

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page page--wide">
        <div className="page__header">
          <h1>Threat Center</h1>
          <p className="page__subtitle">
            What the policy engine has actually stopped — blocked calls, the policies they
            violated, and when. Not a claim to detect adversarial intent, only real policy
            enforcement.
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
        ) : !summary || summary.blocked_calls_total === 0 ? (
          <EmptyState icon={ThreatIcon} title="No blocked calls in this window">
            Nothing has been blocked recently — either your policies are permissive, or nothing
            risky has been attempted. Both are real states, not an error.
          </EmptyState>
        ) : (
          <>
            <div className="stat-grid">
              <div className="stat-card stat-card--danger">
                <ThreatIcon className="stat-card__icon" width={20} height={20} />
                <span className="stat-card__value">{summary.blocked_calls_total}</span>
                <span className="stat-card__label">Blocked calls ({windowDays}d)</span>
              </div>
              <div className="stat-card">
                <span className="stat-card__value">{summary.top_violated_policies.length}</span>
                <span className="stat-card__label">Policies violated</span>
              </div>
            </div>

            <section className="threat-timeline">
              <h2>Blocked calls, daily</h2>
              <div className="threat-timeline__chart">
                {summary.timeline.map((bucket) => (
                  <div key={bucket.day} className="threat-timeline__bar-wrap" title={bucket.day}>
                    <div
                      className="threat-timeline__bar"
                      style={{ height: `${(bucket.blocked_count / maxBlocked) * 100}%` }}
                    />
                    <span className="threat-timeline__count">{bucket.blocked_count}</span>
                  </div>
                ))}
              </div>
            </section>

            <section className="overview-recent">
              <h2>Top violated policies</h2>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Policy</th>
                      <th>Blocked calls</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.top_violated_policies.map((p) => (
                      <tr key={p.policy_id}>
                        <td>
                          <Link to="/policies">{p.policy_name}</Link>
                        </td>
                        <td>{p.block_count}</td>
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
