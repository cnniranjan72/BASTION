import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import {
  forceCenter,
  forceLink,
  forceManyBody,
  forceSimulation,
  type Simulation,
} from "d3-force-3d";
import { useShallow } from "zustand/react/shallow";
import { useGraphStore } from "../../store/graph";
import { NodeMesh } from "./NodeMesh";
import { EdgeLine } from "./EdgeLine";
import { SimulationRegistryContext, type SimNode } from "./SimulationContext";

interface SimLink {
  source: string;
  target: string;
}

/** Owns the one d3-force-3d simulation for the whole graph and ticks it
 * every frame. Structural sync (new node/edge appearing) happens in an
 * effect keyed on the actual *set* of span_ids/edges, not the store's Map
 * identity (which changes on every delta, including pure attribute
 * updates that shouldn't touch the physics graph at all). */
export function ForceGraph() {
  // Array.from(...) allocates a new array every call — without useShallow,
  // useSyncExternalStore (which zustand is built on) sees a "changed"
  // snapshot on every render even when the actual key set didn't change,
  // which is an infinite render loop, not just a wasted render.
  const nodeIds = useGraphStore(useShallow((s) => Array.from(s.nodes.keys())));
  const edges = useGraphStore((s) => s.edges);
  const nodeIdsKey = nodeIds.slice().sort().join(",");
  const edgesKey = edges.map((e) => `${e.from}>${e.to}`).join(",");

  const simNodesRef = useRef(new Map<string, SimNode>());
  const simulationRef = useRef<Simulation<SimNode> | null>(null);
  if (simulationRef.current === null) {
    simulationRef.current = forceSimulation<SimNode>([])
      .force(
        "charge",
        // distanceMin caps the repulsion at close range — without it, nodes
        // spawned in the same shell (replay loads a whole trace's nodes in
        // one effect run, not one at a time like a live trace) can start
        // almost coincident, and the inverse-square charge force blows up
        // toward the near-zero-distance singularity, flinging nodes off
        // camera in the first few ticks before forceCenter can pull them
        // back. distanceMax stops the same thing happening in reverse once
        // nodes are already spread out.
        forceManyBody<SimNode>().strength(-12).distanceMin(1).distanceMax(5),
      )
      .force("center", forceCenter(0, 0, 0).strength(0.15))
      .force(
        "link",
        forceLink<SimNode, SimLink>([])
          .id((n) => n.id)
          .distance(2.2)
          .strength(0.6),
      );
  }
  const simulation = simulationRef.current;

  const registry = useMemo(() => ({ simulation, simNodes: simNodesRef.current }), [simulation]);

  useEffect(() => {
    const simNodes = simNodesRef.current;
    for (const id of nodeIds) {
      if (!simNodes.has(id)) {
        // Spawn new nodes near the origin in a random shell so the
        // simulation has somewhere non-degenerate to push them out from,
        // rather than everything stacking at (0,0,0).
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        const r = 0.5;
        simNodes.set(id, {
          id,
          x: r * Math.sin(phi) * Math.cos(theta),
          y: r * Math.sin(phi) * Math.sin(theta),
          z: r * Math.cos(phi),
        });
      }
    }
    simulation.nodes(Array.from(simNodes.values()));

    const linkForce = simulation.force("link") as ReturnType<typeof forceLink<SimNode, SimLink>>;
    linkForce.links(edges.map((e) => ({ source: e.from, target: e.to })));

    simulation.alpha(0.6).restart();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on stable string forms, not the raw arrays
  }, [nodeIdsKey, edgesKey, simulation]);

  useFrame(() => {
    simulation.tick();
  });

  return (
    <SimulationRegistryContext.Provider value={registry}>
      {edges.map((edge) => (
        <EdgeLine key={`${edge.from}>${edge.to}`} from={edge.from} to={edge.to} />
      ))}
      {nodeIds.map((id) => (
        <NodeMesh key={id} spanId={id} />
      ))}
    </SimulationRegistryContext.Provider>
  );
}
