import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuthStore } from "../store/auth";
import { toast } from "../store/toast";
import { useCommandPaletteStore } from "../store/commandPalette";
import {
  AccountIcon,
  AgentsIcon,
  AnalyticsIcon,
  ApprovalsIcon,
  CostIcon,
  DocsIcon,
  GraphIcon,
  HealthIcon,
  OverviewIcon,
  PoliciesIcon,
  SearchIcon,
  TeamIcon,
  ThreatIcon,
  TracesIcon,
} from "./icons";
import type { ConnectionStatus } from "../hooks/useLiveGraph";

// U16 (v2 upgrade): grouped nav per FRONTEND_V2.md's structure (OPERATE /
// CONTROL / SECURITY / ANALYZE / ADMIN). Two of the spec's illustrative
// labels don't get their own entry: "Incidents" (Incident Replay is
// reached via Traces -> a specific trace, not a separate incidents index
// page) and "Audit Log" (the real audit trail is the event log, already
// reachable per-trace under Traces -- no separate audit-log screen exists
// to link to). Both are real gaps, not silently hidden; adding a
// standalone page for either is real, separate scope.
const NAV_GROUPS: Array<{
  label: string | null;
  links: Array<{ to: string; label: string; end?: boolean; icon: typeof OverviewIcon }>;
}> = [
  { label: null, links: [{ to: "/", label: "Overview", end: true, icon: OverviewIcon }] },
  {
    label: "Operate",
    links: [
      { to: "/graph", label: "Live", icon: GraphIcon },
      { to: "/traces", label: "Traces", icon: TracesIcon },
      { to: "/approvals", label: "Approvals", icon: ApprovalsIcon },
    ],
  },
  {
    label: "Control",
    links: [
      { to: "/agents", label: "Agents", icon: AgentsIcon },
      { to: "/policies", label: "Policies", icon: PoliciesIcon },
      { to: "/policy-studio", label: "Policy Simulator", icon: PoliciesIcon },
    ],
  },
  {
    label: "Security",
    links: [{ to: "/threats", label: "Threats", icon: ThreatIcon }],
  },
  {
    label: "Analyze",
    links: [
      { to: "/analytics", label: "Analytics", icon: AnalyticsIcon },
      { to: "/costs", label: "Costs", icon: CostIcon },
      { to: "/agents", label: "Agent Health", icon: HealthIcon },
    ],
  },
  {
    label: "Admin",
    links: [
      { to: "/team", label: "Team", icon: TeamIcon },
      { to: "/docs", label: "Docs", icon: DocsIcon },
    ],
  },
];

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: "Connecting…",
  open: "Live",
  reconnecting: "Reconnecting…",
  closed: "Not connected",
  error: "Connection error",
};

interface TopBarProps {
  liveStatus: ConnectionStatus | null;
}

export function TopBar({ liveStatus }: TopBarProps) {
  const { role, orgId, refreshToken, logout } = useAuthStore();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const openPalette = useCommandPaletteStore((s) => s.toggle);

  async function handleLogout() {
    if (refreshToken) {
      try {
        await api.logout(refreshToken);
      } catch {
        // best-effort — log out locally regardless of whether the server call succeeded
      }
    }
    logout();
    toast.info("Signed out");
    navigate("/login");
  }

  return (
    <header className="topbar">
      <div className="topbar__brand">
        <span className="topbar__brand-mark" aria-hidden="true" />
        BASTION
      </div>

      <button
        className="topbar__menu-toggle"
        aria-label="Toggle navigation"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((v) => !v)}
      >
        <span />
        <span />
        <span />
      </button>

      <button className="topbar__search" onClick={openPalette} aria-label="Open command palette">
        <SearchIcon width={14} height={14} />
        <span>Jump to…</span>
        <kbd>⌘K</kbd>
      </button>

      <nav className={`topbar__nav${menuOpen ? " is-open" : ""}`}>
        {NAV_GROUPS.map((group) => (
          <div className="topbar__nav-group" key={group.label ?? "root"}>
            {group.label && <span className="topbar__nav-group-label">{group.label}</span>}
            {group.links.map((link) => (
              <NavLink
                key={link.to + link.label}
                to={link.to}
                end={link.end}
                onClick={() => setMenuOpen(false)}
                className={({ isActive }) => `topbar__nav-link${isActive ? " is-active" : ""}`}
              >
                <link.icon width={16} height={16} />
                {link.label}
              </NavLink>
            ))}
          </div>
        ))}
        <div className="topbar__nav-divider" />
        <div className="topbar__user topbar__user--mobile">
          <span className="topbar__role">{role}</span>
          <span className="topbar__org">org {orgId?.slice(0, 8)}</span>
          <button
            onClick={() => {
              navigate("/account");
              setMenuOpen(false);
            }}
          >
            <AccountIcon width={14} height={14} /> Account
          </button>
          <button onClick={handleLogout}>Sign out</button>
        </div>
      </nav>

      {liveStatus && (
        <div className={`topbar__status topbar__status--${liveStatus}`}>
          <span className="topbar__status-dot" />
          {STATUS_LABEL[liveStatus]}
        </div>
      )}
      <div className="topbar__spacer" />
      <div className="topbar__user topbar__user--desktop">
        <span className="topbar__role">{role}</span>
        <span className="topbar__org">org {orgId?.slice(0, 8)}</span>
        <button
          className="topbar__icon-button"
          onClick={() => navigate("/account")}
          aria-label="Account"
        >
          <AccountIcon width={16} height={16} />
        </button>
        <button onClick={handleLogout}>Sign out</button>
      </div>
    </header>
  );
}
