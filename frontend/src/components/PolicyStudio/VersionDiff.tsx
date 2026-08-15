import { useState } from "react";
import type { Agent, Policy } from "../../api/types";

interface VersionDiffProps {
  versions: Policy[]; // one policy_set_id's versions, any order
  agents: Agent[];
}

function ruleKey(rule: Policy["definition"][number], i: number): string {
  return `${i}:${rule.match.tool}`;
}

/** U15 (v2 upgrade), Policy Studio's version diff — FRONTEND_V2.md: "show
 * v12 → v13 field-by-field changes ... and which agents are affected."
 * Backed by U4's real optimistic-concurrency versioning (every row here
 * is a real, immutable, previously-persisted version — ADR-016).
 *
 * Scope, stated explicitly (not silently omitted): "who changed it" isn't
 * shown — the `policies` table has no `created_by` column (confirmed:
 * infra/db/migrations/0002_policies.sql), so there's no real data to
 * display; inventing an attribution here would violate the same
 * no-mock-data rule this whole phase is built around. See
 * docs/PROGRESS.md's U15 entry. */
export function VersionDiff({ versions, agents }: VersionDiffProps) {
  const sorted = [...versions].sort((a, b) => a.version - b.version);
  const [fromId, setFromId] = useState(sorted[sorted.length - 2]?.id ?? sorted[0]?.id ?? "");
  const [toId, setToId] = useState(sorted[sorted.length - 1]?.id ?? "");

  const from = sorted.find((v) => v.id === fromId);
  const to = sorted.find((v) => v.id === toId);

  const affectedAgents = to ? agents.filter((a) => a.policy_set_id === to.policy_set_id) : [];

  return (
    <div className="version-diff">
      <div className="version-diff__pickers">
        <select value={fromId} onChange={(e) => setFromId(e.target.value)}>
          {sorted.map((v) => (
            <option key={v.id} value={v.id}>
              v{v.version}
            </option>
          ))}
        </select>
        <span>→</span>
        <select value={toId} onChange={(e) => setToId(e.target.value)}>
          {sorted.map((v) => (
            <option key={v.id} value={v.id}>
              v{v.version}
            </option>
          ))}
        </select>
      </div>

      {from && to && (
        <>
          {from.id === to.id ? (
            <p className="version-diff__empty">Choose two different versions to compare.</p>
          ) : (
            <RuleDiff from={from.definition} to={to.definition} />
          )}
          <p className="version-diff__affected">
            Affects {affectedAgents.length} agent{affectedAgents.length === 1 ? "" : "s"} currently
            assigned to this policy set
            {affectedAgents.length > 0 && <>: {affectedAgents.map((a) => a.name).join(", ")}</>}
          </p>
        </>
      )}
    </div>
  );
}

function RuleDiff({ from, to }: { from: Policy["definition"]; to: Policy["definition"] }) {
  const maxLen = Math.max(from.length, to.length);
  const rows = [];
  for (let i = 0; i < maxLen; i++) {
    const a = from[i];
    const b = to[i];
    if (!a && b) {
      rows.push(
        <div className="version-diff__row version-diff__row--added" key={ruleKey(b, i)}>
          + {b.match.tool} → {b.action}
        </div>,
      );
    } else if (a && !b) {
      rows.push(
        <div className="version-diff__row version-diff__row--removed" key={ruleKey(a, i)}>
          − {a.match.tool} → {a.action}
        </div>,
      );
    } else if (a && b) {
      const same = JSON.stringify(a) === JSON.stringify(b);
      if (same) {
        rows.push(
          <div className="version-diff__row" key={ruleKey(a, i)}>
            &nbsp;&nbsp;{a.match.tool} → {a.action} (unchanged)
          </div>,
        );
      } else {
        rows.push(
          <div className="version-diff__row version-diff__row--changed" key={ruleKey(a, i)}>
            <div className="version-diff__row--removed">
              − {a.match.tool} → {a.action}
              {a.condition ? ` IF ${a.condition}` : ""}
              {a.limits ? ` limits=${JSON.stringify(a.limits)}` : ""}
            </div>
            <div className="version-diff__row--added">
              + {b.match.tool} → {b.action}
              {b.condition ? ` IF ${b.condition}` : ""}
              {b.limits ? ` limits=${JSON.stringify(b.limits)}` : ""}
            </div>
          </div>,
        );
      }
    }
  }
  return <div className="version-diff__rules">{rows}</div>;
}
