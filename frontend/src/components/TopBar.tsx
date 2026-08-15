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
  GraphIcon,
  OverviewIcon,
  PoliciesIcon,
  SearchIcon,
  TeamIcon,
  TracesIcon,
} from "./icons";
import type { ConnectionStatus } from "../hooks/useLiveGraph";

const NAV_LINKS = [
  { to: "/", label: "Overview", end: true, icon: OverviewIcon },
  { to: "/graph", label: "Graph", icon: GraphIcon },
  { to: "/traces", label: "Traces", icon: TracesIcon },
  { to: "/analytics", label: "Analytics", icon: AnalyticsIcon },
  { to: "/agents", label: "Agents", icon: AgentsIcon },
  { to: "/policies", label: "Policies", icon: PoliciesIcon },
  { to: "/policy-studio", label: "Policy Studio", icon: PoliciesIcon },
  { to: "/approvals", label: "Approvals", icon: ApprovalsIcon },
  { to: "/team", label: "Team", icon: TeamIcon },
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
        {NAV_LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            onClick={() => setMenuOpen(false)}
            className={({ isActive }) => `topbar__nav-link${isActive ? " is-active" : ""}`}
          >
            <link.icon width={16} height={16} />
            {link.label}
          </NavLink>
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
