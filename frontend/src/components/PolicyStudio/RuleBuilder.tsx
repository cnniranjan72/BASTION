import type { PolicyRule } from "../../api/types";

interface RuleBuilderProps {
  rules: PolicyRule[];
  onChange: (rules: PolicyRule[]) => void;
}

const EMPTY_RULE: PolicyRule = { match: { tool: "*" }, action: "allow" };

/** U15 (v2 upgrade), Policy Studio — FRONTEND_V2.md: "WHEN <agent> calls
 * <tool> IF <condition> THEN <action> composed from dropdowns/structured
 * inputs, compiling to the same YAML the interceptor consumes." Emits the
 * exact `PolicyRule[]` shape POST /policies already accepts — no
 * intermediate UI-only representation to keep in sync separately. */
export function RuleBuilder({ rules, onChange }: RuleBuilderProps) {
  function updateRule(index: number, patch: Partial<PolicyRule>) {
    onChange(rules.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }
  function updateMatch(index: number, patch: Partial<PolicyRule["match"]>) {
    updateRule(index, { match: { ...rules[index]!.match, ...patch } });
  }
  function updateLimits(index: number, patch: Partial<NonNullable<PolicyRule["limits"]>>) {
    updateRule(index, { limits: { ...rules[index]!.limits, ...patch } });
  }
  function removeRule(index: number) {
    onChange(rules.filter((_, i) => i !== index));
  }
  function addRule() {
    onChange([...rules, { ...EMPTY_RULE }]);
  }
  function moveRule(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= rules.length) return;
    const next = [...rules];
    [next[index], next[target]] = [next[target]!, next[index]!];
    onChange(next);
  }

  return (
    <div className="rule-builder">
      {rules.map((rule, i) => (
        <div className="rule-builder__row" key={i}>
          <span className="rule-builder__seq">{i + 1}</span>
          <span className="rule-builder__when">WHEN tool</span>
          <input
            className="rule-builder__tool"
            value={rule.match.tool}
            onChange={(e) => updateMatch(i, { tool: e.target.value })}
            placeholder="* or tool name"
          />
          <input
            className="rule-builder__pattern"
            value={rule.match.pattern ?? ""}
            onChange={(e) => updateMatch(i, { pattern: e.target.value || undefined })}
            placeholder="pattern (optional regex)"
          />
          <span className="rule-builder__when">IF</span>
          <input
            className="rule-builder__condition"
            value={rule.condition ?? ""}
            onChange={(e) => updateRule(i, { condition: e.target.value || undefined })}
            placeholder='condition (e.g. "amount > 100")'
          />
          <span className="rule-builder__when">THEN</span>
          <select
            value={rule.action}
            onChange={(e) => updateRule(i, { action: e.target.value as PolicyRule["action"] })}
          >
            <option value="allow">allow</option>
            <option value="block">block</option>
            <option value="require_approval">require_approval</option>
          </select>
          <div className="rule-builder__actions">
            <button
              type="button"
              onClick={() => moveRule(i, -1)}
              disabled={i === 0}
              title="Move up"
            >
              ↑
            </button>
            <button
              type="button"
              onClick={() => moveRule(i, 1)}
              disabled={i === rules.length - 1}
              title="Move down"
            >
              ↓
            </button>
            <button type="button" onClick={() => removeRule(i)} title="Remove rule">
              ×
            </button>
          </div>

          {rule.action === "allow" && (
            <div className="rule-builder__limits">
              <span className="rule-builder__limits-label">limits (optional):</span>
              <label>
                calls/min
                <input
                  type="number"
                  min={0}
                  value={rule.limits?.calls_per_minute ?? ""}
                  onChange={(e) =>
                    updateLimits(i, {
                      calls_per_minute: e.target.value ? Number(e.target.value) : null,
                    })
                  }
                />
              </label>
              <label>
                max txn $
                <input
                  type="number"
                  min={0}
                  value={rule.limits?.max_transaction_amount ?? ""}
                  onChange={(e) =>
                    updateLimits(i, {
                      max_transaction_amount: e.target.value ? Number(e.target.value) : null,
                    })
                  }
                />
              </label>
              <label>
                org spend/day $
                <input
                  type="number"
                  min={0}
                  value={rule.limits?.org_spend_per_day ?? ""}
                  onChange={(e) =>
                    updateLimits(i, {
                      org_spend_per_day: e.target.value ? Number(e.target.value) : null,
                    })
                  }
                />
              </label>
              <label>
                LLM $/hr
                <input
                  type="number"
                  min={0}
                  value={rule.limits?.agent_llm_budget_per_hour ?? ""}
                  onChange={(e) =>
                    updateLimits(i, {
                      agent_llm_budget_per_hour: e.target.value ? Number(e.target.value) : null,
                    })
                  }
                />
              </label>
            </div>
          )}
        </div>
      ))}
      <button type="button" className="rule-builder__add" onClick={addRule}>
        + Add rule
      </button>
    </div>
  );
}
