import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import { AreaChart } from "./charts/AreaChart";
import { BarList } from "./charts/BarList";
import { DonutGauge } from "./charts/DonutGauge";
import { TopBar } from "./TopBar";
import type { Agent, TraceSummary } from "../api/types";

function dayKey(iso: string): string {
  return iso.slice(0, 10);
}

function lastNDays(n: number): string[] {
  const days: string[] = [];
  const now = new Date();
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(now.getDate() - i);
    days.push(d.toISOString().slice(0, 10));
  }
  return days;
}

export function AnalyticsPage() {
  const [traces, setTraces] = useState<TraceSummary[] | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.listTraces(), api.listAgents()])
      .then(([t, a]) => {
        setTraces(t);
        setAgents(a);
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load analytics");
      });
  }, []);

  const days = useMemo(() => lastNDays(14), []);

  const byDay = useMemo(() => {
    const map = new Map<string, { calls: number; cost: number; blocked: number }>();
    for (const day of days) map.set(day, { calls: 0, cost: 0, blocked: 0 });
    for (const t of traces ?? []) {
      const key = dayKey(t.started_at);
      const bucket = map.get(key);
      if (bucket) {
        bucket.calls += t.total_calls;
        bucket.cost += t.total_cost;
        bucket.blocked += t.blocked_calls;
      }
    }
    return days.map((d) => ({ day: d, ...map.get(d)! }));
  }, [traces, days]);

  const totalCalls = traces?.reduce((s, t) => s + t.total_calls, 0) ?? 0;
  const totalBlocked = traces?.reduce((s, t) => s + t.blocked_calls, 0) ?? 0;
  const totalCost = traces?.reduce((s, t) => s + t.total_cost, 0) ?? 0;
  const blockRate = totalCalls > 0 ? totalBlocked / totalCalls : 0;

  const topAgents = useMemo(() => {
    const byId = new Map(agents.map((a) => [a.id, a.name]));
    const counts = new Map<string, number>();
    for (const t of traces ?? []) {
      counts.set(t.agent_id, (counts.get(t.agent_id) ?? 0) + t.total_calls);
    }
    return Array.from(counts.entries())
      .map(([id, value]) => ({ label: byId.get(id) ?? id.slice(0, 8), value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 6);
  }, [traces, agents]);

  const dayLabels = days.map((d) =>
    new Date(d).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
  );

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page page--wide">
        <div className="page__header">
          <h1>Analytics</h1>
          <p className="page__subtitle">
            Call volume, spend, and block rate across every trace, last 14 days.
          </p>
        </div>

        {error && <p className="page__error">{error}</p>}

        {traces === null ? (
          <p className="page__empty">Loading…</p>
        ) : traces.length === 0 ? (
          <p className="page__empty">No traces yet — analytics fill in once agents start calling.</p>
        ) : (
          <div className="analytics-grid">
            <div className="chart-card chart-card--wide">
              <h2>Calls per day</h2>
              <AreaChart
                points={byDay.map((d) => d.calls)}
                labels={dayLabels}
                color="var(--accent)"
              />
            </div>

            <div className="chart-card">
              <h2>Block rate</h2>
              <DonutGauge
                fraction={blockRate}
                color="var(--danger)"
                trackColor="var(--bg-elevated)"
                label={`${totalBlocked} of ${totalCalls}`}
                centerValue={`${(blockRate * 100).toFixed(0)}%`}
              />
            </div>

            <div className="chart-card chart-card--wide">
              <h2>Cost per day</h2>
              <AreaChart
                points={byDay.map((d) => d.cost)}
                labels={dayLabels}
                color="var(--accent-2)"
                formatValue={(v) => `$${v.toFixed(4)}`}
              />
            </div>

            <div className="chart-card">
              <h2>Top agents by calls</h2>
              {topAgents.length === 0 ? (
                <p className="page__empty">No calls yet.</p>
              ) : (
                <BarList items={topAgents} color="var(--accent)" />
              )}
            </div>

            <div className="chart-card chart-card--stats">
              <h2>Totals</h2>
              <dl className="stat-list">
                <div>
                  <dt>Total calls</dt>
                  <dd>{totalCalls}</dd>
                </div>
                <div>
                  <dt>Blocked calls</dt>
                  <dd>{totalBlocked}</dd>
                </div>
                <div>
                  <dt>Total cost</dt>
                  <dd>${totalCost.toFixed(4)}</dd>
                </div>
                <div>
                  <dt>Traces recorded</dt>
                  <dd>{traces.length}</dd>
                </div>
              </dl>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
