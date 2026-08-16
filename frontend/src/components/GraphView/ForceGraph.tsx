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
    // numDimensions=3 is not optional decoration — d3-force-3d defaults to
    // **2** (see types/d3-force-3d.d.ts's comment on this exact bug): omit
    // it and forceManyBody/forceLink silently never touch z at all, no
    // matter how "3D" everything else here looks.
    simulationRef.current = forceSimulation<SimNode>([], 3)
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
        // Spawn new nodes in a random shell, radius growing with spawn
        // order (d3-force's own initializeNodes fallback uses this exact
        // Math.cbrt(0.5 + i) scaling for 3D, for the same reason) rather
        // than one fixed small radius for every node. A fixed r=0.5 shell
        // put several nodes from the same resync/live burst within
        // forceManyBody's distanceMin of each other — confirmed in
        // production, not theoretical: one such burst put a node at
        // (x=-7133, y=15584) before the position clamp below existed, and
        // even after clamping, several nodes converged into one
        // indistinguishable overlapping cluster instead of a legible
        // graph. Spacing the spawn points out is the actual fix for
        // *that* (the clamp only guarantees they stay on-screen, not that
        // they're visually distinguishable).
        const spawnIndex = simNodes.size;
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        const r = 1.5 * Math.cbrt(0.5 + spawnIndex);
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
    // Confirmed in production, not theoretical: several nodes spawned
    // together (a resync burst on reconnect, or a live demo's rapid tool
    // calls) can genuinely explode under forceManyBody's repulsion —
    // `distanceMin` caps a single close pair's *force magnitude*, not the
    // *accumulated* velocity from several simultaneous close neighbors
    // compounding within one high-alpha tick. One such node was found
    // sitting at (x=-7133, y=15584) after a 4-node burst — a real
    // computed value, not NaN, so NodeMesh's finite-check guard doesn't
    // catch it; the physics itself needs a hard bound, not just the
    // render path. Clamping (and zeroing residual velocity so a clamped
    // node doesn't immediately re-launch next tick) is a robust backstop
    // regardless of how well the force constants above are ever tuned —
    // OrbitControls' own maxDistance is 30, so nothing here needs to be
    // further out than that to stay visible.
    const MAX_POSITION = 15;
    for (const node of simNodesRef.current.values()) {
      const rawX = node.x ?? 0;
      const rawY = node.y ?? 0;
      const rawZ = node.z ?? 0;
      const x = Number.isFinite(rawX) ? rawX : 0;
      const y = Number.isFinite(rawY) ? rawY : 0;
      const z = Number.isFinite(rawZ) ? rawZ : 0;
      const clampedX = Math.min(MAX_POSITION, Math.max(-MAX_POSITION, x));
      const clampedY = Math.min(MAX_POSITION, Math.max(-MAX_POSITION, y));
      const clampedZ = Math.min(MAX_POSITION, Math.max(-MAX_POSITION, z));
      if (clampedX !== node.x || clampedY !== node.y || clampedZ !== node.z) {
        node.x = clampedX;
        node.y = clampedY;
        node.z = clampedZ;
        node.vx = 0;
        node.vy = 0;
        node.vz = 0;
      }
    }
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
