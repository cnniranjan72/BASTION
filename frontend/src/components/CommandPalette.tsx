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
  { label: "Overview", to: "/", hint: "Org summary and onboarding" },
  { label: "Graph", to: "/graph", hint: "Live / replayed 3D execution graph" },
  { label: "Traces", to: "/traces", hint: "Search every recorded trace" },
  { label: "Analytics", to: "/analytics", hint: "Call volume, cost, block rate" },
  { label: "Agents", to: "/agents", hint: "API keys and policy assignment" },
  { label: "Policies", to: "/policies", hint: "Versioned policy definitions" },
  { label: "Approvals", to: "/approvals", hint: "Calls waiting on a human" },
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
        setTraces(t.slice(0, 8));
      })
      .catch(() => {
        // Best-effort — the palette still works for static page nav if this fails.
      });
  }, [open, accessToken]);

  if (!accessToken) return null;

  const commands: Command[] = [
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
    ...traces.map((t) => ({
      id: `trace:${t.trace_id}`,
      label: `Trace ${t.trace_id.slice(0, 8)}`,
      hint: `${TRACE_STATUS_LABEL[t.status]} · ${t.total_calls} calls${t.blocked_calls ? ` · ${t.blocked_calls} blocked` : ""}`,
      group: "Recent traces",
      action: () => navigate(`/graph?trace=${t.trace_id}`),
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
