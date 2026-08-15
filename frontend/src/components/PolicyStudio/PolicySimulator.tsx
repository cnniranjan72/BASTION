import { useState } from "react";
import { api, ApiError } from "../../api/client";
import type { Agent, SimulatePolicyResponse } from "../../api/types";

interface PolicySimulatorProps {
  agents: Agent[];
}

const DECISION_LABEL: Record<SimulatePolicyResponse["decision"], string> = {
  allow: "ALLOWED",
  block: "BLOCKED",
  require_approval: "REQUIRES APPROVAL",
};

/** U15 (v2 upgrade), Policy Studio's simulator — FRONTEND_V2.md: "paste a
 * hypothetical tool call ... and see it walk through the actual
 * evaluation chain ... using the real policy engine." Calls POST
 * /policies/simulate directly — real policy_cache + evaluate(), never a
 * client-side approximation of the DSL. See docs/adr/ADR-020 for why
 * `configured_limits` is shown informationally rather than actually
 * checked against live Redis state. */
export function PolicySimulator({ agents }: PolicySimulatorProps) {
  const [agentId, setAgentId] = useState("");
  const [toolName, setToolName] = useState("");
  const [argsText, setArgsText] = useState("{}");
  const [result, setResult] = useState<SimulatePolicyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  async function runSimulation() {
    setError(null);
    setResult(null);
    if (!agentId || !toolName.trim()) {
      setError("Choose an agent and enter a tool name.");
      return;
    }
    let args: Record<string, unknown>;
    try {
      args = JSON.parse(argsText || "{}");
    } catch {
      setError("Args must be valid JSON.");
      return;
    }
    setRunning(true);
    try {
      setResult(await api.simulatePolicy(agentId, toolName.trim(), args));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Simulation failed");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="policy-simulator">
      <div className="policy-simulator__form">
        <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
          <option value="">Choose an agent…</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        <input
          placeholder="tool_name, e.g. payments.transfer"
          value={toolName}
          onChange={(e) => setToolName(e.target.value)}
        />
        <textarea
          rows={3}
          value={argsText}
          onChange={(e) => setArgsText(e.target.value)}
          spellCheck={false}
          placeholder='{ "amount": 150 }'
        />
        <button onClick={runSimulation} disabled={running}>
          {running ? "Simulating…" : "Simulate"}
        </button>
      </div>

      {error && <p className="page__error">{error}</p>}

      {result && (
        <div className={`policy-simulator__result policy-simulator__result--${result.decision}`}>
          <div className="policy-simulator__decision">{DECISION_LABEL[result.decision]}</div>
          {result.matched_rule_tool && (
            <p className="policy-simulator__detail">
              Matched rule for tool <code>{result.matched_rule_tool}</code>
            </p>
          )}
          {result.reason && <p className="policy-simulator__detail">{result.reason}</p>}
          {result.configured_limits && (
            <div className="policy-simulator__limits">
              <span>Configured limits (not applied — simulation only):</span>
              <pre className="inspector__json">
                {JSON.stringify(result.configured_limits, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
