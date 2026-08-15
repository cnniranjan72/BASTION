import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuthStore } from "../store/auth";
import { useCommandPaletteStore } from "../store/commandPalette";
import { TRACE_STATUS_LABEL } from "../lib/labels";
import type { Agent, TraceSummary } from "../api/types";

interface Command {
  id: string;
  label: string;
  hint: string;
  group: string;
  action: () => void;
}

const PAGES: Array<{ label: string; to: string; hint: string }> = [
  { label: "Command Center", to: "/", hint: "Org summary and live snapshot" },
  { label: "Live", to: "/graph", hint: "Live / replayed 3D execution graph" },
  { label: "Trace Explorer", to: "/traces", hint: "Search every recorded trace" },
  { label: "Approval Center", to: "/approvals", hint: "Calls waiting on a human" },
  { label: "Agents", to: "/agents", hint: "API keys and policy assignment" },
  { label: "Policies", to: "/policies", hint: "Versioned policy definitions" },
  { label: "Policy Simulator", to: "/policy-studio", hint: "Build, simulate, diff policies" },
  { label: "Threat Center", to: "/threats", hint: "Blocked calls and violated policies" },
  { label: "Analytics", to: "/analytics", hint: "Call volume, cost, block rate" },
  { label: "Cost Center", to: "/costs", hint: "Spend by agent and tool" },
  { label: "Team", to: "/team", hint: "Teammates and roles" },
  { label: "Account", to: "/account", hint: "Profile, password, API tokens" },
];

export function CommandPalette() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const navigate = useNavigate();
  const open = useCommandPaletteStore((s) => s.open);
  const setOpen = useCommandPaletteStore((s) => s.setOpen);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const isCombo = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      if (isCombo && accessToken) {
        e.preventDefault();
        useCommandPaletteStore.getState().toggle();
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [accessToken, setOpen]);

  useEffect(() => {
    if (!open || !accessToken) return;
    setQuery("");
    setSelected(0);
    requestAnimationFrame(() => inputRef.current?.focus());
    Promise.all([api.listAgents(), api.listTraces()])
      .then(([a, t]) => {
        setAgents(a);
        setTraces(t);
      })
      .catch(() => {
        // Best-effort — the palette still works for static page nav if this fails.
      });
  }, [open, accessToken]);

  if (!accessToken) return null;

  // U16 (v2 upgrade), FRONTEND_V2.md's command palette spec: "show blocked
  // calls, show pending approvals, replay last incident, create a policy —
  // all real navigation/actions, not a static list." lastIncident is a
  // real computation over the traces already fetched above (newest
  // had_blocks or failed trace), not a hardcoded link.
  const lastIncident = traces
    .filter((t) => t.status === "had_blocks" || t.status === "failed")
    .sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime())[0];
  const blockedTraceCount = traces.filter((t) => t.blocked_calls > 0).length;

  const actions: Command[] = [
    {
      id: "action:blocked-calls",
      label: "Show blocked calls",
      hint: `${blockedTraceCount} trace${blockedTraceCount === 1 ? "" : "s"} with a block`,
      group: "Actions",
      action: () => navigate("/traces?status=had_blocks"),
    },
    {
      id: "action:pending-approvals",
      label: "Show pending approvals",
      hint: "Jump to the Approval Center",
      group: "Actions",
      action: () => navigate("/approvals"),
    },
    {
      id: "action:create-policy",
      label: "Create a policy",
      hint: "Open Policy Simulator's rule builder",
      group: "Actions",
      action: () => navigate("/policy-studio"),
    },
    ...(lastIncident
      ? [
          {
            id: "action:replay-last-incident",
            label: "Replay last incident",
            hint: `${TRACE_STATUS_LABEL[lastIncident.status]} · ${new Date(lastIncident.started_at).toLocaleString()}`,
            group: "Actions",
            action: () => navigate(`/replay/${lastIncident.trace_id}`),
          },
        ]
      : []),
  ];

  const commands: Command[] = [
    ...actions,
    ...PAGES.map((p) => ({
      id: `page:${p.to}`,
      label: p.label,
      hint: p.hint,
      group: "Pages",
      action: () => navigate(p.to),
    })),
    ...agents.map((a) => ({
      id: `agent:${a.id}`,
      label: a.name,
      hint: "Agent — jump to Agents page",
      group: "Agents",
      action: () => navigate("/agents"),
    })),
    ...traces.slice(0, 8).map((t) => ({
      id: `trace:${t.trace_id}`,
      label: `Trace ${t.trace_id.slice(0, 8)}`,
      hint: `${TRACE_STATUS_LABEL[t.status]} · ${t.total_calls} calls${t.blocked_calls ? ` · ${t.blocked_calls} blocked` : ""}`,
      group: "Recent traces",
      action: () => navigate(`/replay/${t.trace_id}`),
    })),
  ];

  const q = query.trim().toLowerCase();
  const filtered = q
    ? commands.filter((c) => c.label.toLowerCase().includes(q) || c.hint.toLowerCase().includes(q))
    : commands;

  function runSelected() {
    const cmd = filtered[selected];
    if (cmd) {
      cmd.action();
      setOpen(false);
    }
  }

  function onInputKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((s) => Math.min(s + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((s) => Math.max(s - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      runSelected();
    }
  }

  if (!open) return null;

  let lastGroup = "";

  return (
    <div className="cmdk-overlay" onClick={() => setOpen(false)}>
      <div className="cmdk" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="cmdk__input"
          placeholder="Jump to a page, agent, or trace…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSelected(0);
          }}
          onKeyDown={onInputKeyDown}
        />
        <div className="cmdk__list">
          {filtered.length === 0 && <p className="cmdk__empty">No matches.</p>}
          {filtered.map((cmd, i) => {
            const showGroup = cmd.group !== lastGroup;
            lastGroup = cmd.group;
            return (
              <div key={cmd.id}>
                {showGroup && <div className="cmdk__group">{cmd.group}</div>}
                <button
                  className={`cmdk__item${i === selected ? " is-selected" : ""}`}
                  onMouseEnter={() => setSelected(i)}
                  onClick={() => {
                    cmd.action();
                    setOpen(false);
                  }}
                >
                  <span className="cmdk__item-label">{cmd.label}</span>
                  <span className="cmdk__item-hint">{cmd.hint}</span>
                </button>
              </div>
            );
          })}
        </div>
        <div className="cmdk__footer">
          <span>↑↓ navigate</span>
          <span>↵ select</span>
          <span>esc close</span>
        </div>
      </div>
    </div>
  );
}
