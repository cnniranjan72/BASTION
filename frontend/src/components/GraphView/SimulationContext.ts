import { createContext, useContext } from "react";
import type { Simulation, SimulationNodeDatum } from "d3-force-3d";

export interface SimNode extends SimulationNodeDatum {
  id: string;
}

export interface SimulationRegistry {
  simulation: Simulation<SimNode>;
  simNodes: Map<string, SimNode>;
}

// Shared between ForceGraph (owns the simulation + ticks it) and every
// NodeMesh/EdgeLine (read a position each frame directly off the sim node
// object) — this is what keeps position updates out of React state
// entirely (ARCHITECTURE.md §2.6: never re-render the whole graph on every
// event, apply deltas to a persistent Three.js scene graph instead).
export const SimulationRegistryContext = createContext<SimulationRegistry | null>(null);

export function useSimulationRegistry(): SimulationRegistry {
  const ctx = useContext(SimulationRegistryContext);
  if (!ctx) throw new Error("useSimulationRegistry must be used within ForceGraph");
  return ctx;
}
