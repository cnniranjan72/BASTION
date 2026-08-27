import { useEffect, useState } from "react";
import type { ComponentType, SVGProps } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useAuthStore } from "../store/auth";
import { useCountUp } from "../hooks/useCountUp";
import { TRACE_STATUS_LABEL } from "../lib/labels";
import {
  AgentsIcon,
  AlertIcon,
  AnalyticsIcon,
  ApprovalsIcon,
  CostIcon,
  GraphIcon,
  PoliciesIcon,
} from "./icons";
import { TopBar } from "./TopBar";
import type { Agent, ApprovalRequest, CommandCenterSnapshot, TraceSummary } from "../api/types";

interface Stats {
  agents: Agent[];
  activePolicySets: number;
  approvals: ApprovalRequest[];
  traces: TraceSummary[];
  purchaseTraces: TraceSummary[];
}

function StatCard({
  to,
  label,
  value,
  tone,
  icon: Icon,
}: {
  to: string;
  label: string;
  value: string | number;
  tone?: "danger" | "warning";
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}) {
  const numeric = typeof value === "number" ? value : null;
  const animated = useCountUp(numeric ?? 0);
  const display = numeric === null ? value : animated;
  return (
    <Link to={to} className={`stat-card${tone ? ` stat-card--${tone}` : ""}`}>
      <Icon className="stat-card__icon" width={20} height={20} />
      <span className="stat-card__value">{display}</span>
      <span className="stat-card__label">{label}</span>
    </Link>
  );
}

function timeAgo(iso: string): string {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const DECISION_LABEL: Record<string, string> = {
  CallAllowed: "ALLOWED",
  CallBlocked: "BLOCKED",
  CallPendingApproval: "APPROVAL",
};

// U16 (v2 upgrade), FRONTEND_V2.md's Command Center: "Live, not
// refresh-based". No new WS channel exists for this org-wide snapshot (the
// existing fan-out is per-agent, `docs/adr/ADR-021`) -- polled every 5s
// instead, which is the honest scoping call made there.
export function OverviewPage() {
  const role = useAuthStore((s) => s.role);
  const [stats, setStats] = useState<Stats | null>(null);
  const [snapshot, setSnapshot] = useState<CommandCenterSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.listAgents(),
      api.listPolicies(),
      api.listApprovals(),
      api.listTraces(),
      // Track 01: the commerce story belongs on the home screen, not three
      // clicks deep — same GET /traces?tool= filter Trace Explorer already
      // uses, no new endpoint. A tool this org has never called just comes
      // back empty, which is the correct "no purchases yet" state, not an
      // error.
      api.listTraces({ tool: "razorpay.purchase" }),
    ])
      .then(([agents, policies, approvals, traces, purchaseTraces]) => {
        const activePolicySets = new Set(policies.filter((p) => p.active).map((p) => p.policy_set_id))
          .size;
        setStats({ agents, activePolicySets, approvals, traces, purchaseTraces });
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load overview");
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    function poll() {
      api
        .getCommandCenterSnapshot()
        .then((s) => {
          if (!cancelled) setSnapshot(s);
        })
        .catch(() => {
          // Best-effort — the rest of the page still works from stats above.
        });
    }
    poll();
    const interval = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const totalCalls = stats?.traces.reduce((sum, t) => sum + t.total_calls, 0) ?? 0;
  const blockedCalls = stats?.traces.reduce((sum, t) => sum + t.blocked_calls, 0) ?? 0;
  const totalCost = stats?.traces.reduce((sum, t) => sum + t.total_cost, 0) ?? 0;
  // A purchase trace that "had_blocks" means the purchase itself was
  // blocked (over-threshold or rate-limited) -- there's nothing else in
  // this single-span trace shape that could produce a block otherwise.
  const purchasesCompleted = stats?.purchaseTraces.filter((t) => t.status === "completed").length ?? 0;
  const purchasesBlocked = stats?.purchaseTraces.filter((t) => t.status === "had_blocks").length ?? 0;

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page page--wide">
        <div className="page__header">
          <h1>Command Center</h1>
          <p className="page__subtitle">Everything BASTION has seen across your organization.</p>
        </div>

        {error && <p className="page__error">{error}</p>}

        {snapshot && (
          <div className="command-strip">
            <span className={`command-strip__item${snapshot.agents_healthy < snapshot.agents_total ? " command-strip__item--warning" : ""}`}>
              <span className="command-strip__dot" /> {snapshot.agents_healthy}/{snapshot.agents_total}{" "}
              agents healthy
            </span>
            <span className="command-strip__item">
              <span className="command-strip__dot" /> {snapshot.availability_pct.toFixed(2)}% call
              success rate
            </span>
            <span className="command-strip__item">
              Last incident{" "}
              {snapshot.last_incident_at ? timeAgo(snapshot.last_incident_at) : "— none recorded"}
            </span>
          </div>
        )}

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
                <Link to="/graph">Watch it live</Link> — connect the graph view to your agent and see
                every call, decision, and block as it happens.
              </li>
            </ol>
          </div>
        ) : (
          <>
            <div className="stat-grid">
              <StatCard to="/agents" label="Agents" value={stats?.agents.length ?? "—"} icon={AgentsIcon} />
              <StatCard
                to="/policies"
                label="Active policies"
                value={stats?.activePolicySets ?? "—"}
                icon={PoliciesIcon}
              />
              <StatCard
                to="/approvals"
                label="Pending approvals"
                value={stats?.approvals.length ?? "—"}
                tone={stats && stats.approvals.length > 0 ? "warning" : undefined}
                icon={ApprovalsIcon}
              />
              <StatCard
                to="/graph"
                label="Blocked calls"
                value={blockedCalls}
                tone={blockedCalls > 0 ? "danger" : undefined}
                icon={AlertIcon}
              />
              <StatCard to="/graph" label="Total calls" value={totalCalls} icon={GraphIcon} />
              <StatCard
                to="/graph"
                label="Total cost"
                value={`$${totalCost.toFixed(4)}`}
                icon={AnalyticsIcon}
              />
              <StatCard
                to="/traces?tool=razorpay.purchase&status=completed"
                label="Purchases completed"
                value={purchasesCompleted}
                icon={CostIcon}
              />
              <StatCard
                to="/traces?tool=razorpay.purchase&status=had_blocks"
                label="Purchases blocked"
                value={purchasesBlocked}
                tone={purchasesBlocked > 0 ? "warning" : undefined}
                icon={AlertIcon}
              />
            </div>

            {snapshot && snapshot.recent_activity.length > 0 && (
              <section className="overview-recent">
                <h2>Live agent activity</h2>
                <ul className="trace-list">
                  {snapshot.recent_activity.map((entry, i) => (
                    <li key={`${entry.agent_id}-${entry.at}-${i}`}>
                      <div
                        className={`trace-list__item trace-list__item--${
                          entry.decision === "CallBlocked" ? "had_blocks" : "completed"
                        }`}
                      >
                        <span className="trace-list__status">
                          {entry.agent_name} — {entry.tool_name} —{" "}
                          {DECISION_LABEL[entry.decision] ?? entry.decision}
                        </span>
                        <span className="trace-list__meta">{timeAgo(entry.at)}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section className="overview-recent">
              <h2>Recent traces</h2>
              {stats && stats.traces.length === 0 ? (
                <p className="page__empty">No completed traces yet.</p>
              ) : (
                <ul className="trace-list">
                  {stats?.traces.slice(0, 6).map((t) => (
                    <li key={t.trace_id}>
                      <Link
                        to={`/replay/${t.trace_id}`}
                        className={`trace-list__item trace-list__item--${t.status}`}
                      >
                        <span className="trace-list__status">{TRACE_STATUS_LABEL[t.status]}</span>
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
