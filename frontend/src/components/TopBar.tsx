import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuthStore } from "../store/auth";
import { toast } from "../store/toast";
import type { ConnectionStatus } from "../hooks/useLiveGraph";

const NAV_LINKS = [
  { to: "/", label: "Graph", end: true },
  { to: "/agents", label: "Agents" },
  { to: "/policies", label: "Policies" },
  { to: "/approvals", label: "Approvals" },
];

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: "Connecting…",
  open: "Live",
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

      <nav className={`topbar__nav${menuOpen ? " is-open" : ""}`}>
        {NAV_LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            onClick={() => setMenuOpen(false)}
            className={({ isActive }) => `topbar__nav-link${isActive ? " is-active" : ""}`}
          >
            {link.label}
          </NavLink>
        ))}
        <div className="topbar__nav-divider" />
        <div className="topbar__user topbar__user--mobile">
          <span className="topbar__role">{role}</span>
          <span className="topbar__org">org {orgId?.slice(0, 8)}</span>
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
        <button onClick={handleLogout}>Sign out</button>
      </div>
    </header>
  );
}
