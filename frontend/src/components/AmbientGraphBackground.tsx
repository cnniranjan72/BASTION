import { useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { BufferGeometry, Float32BufferAttribute, Line, LineBasicMaterial } from "three";
import type { Group } from "three";

// Same status palette as GraphView/encoding.ts (pending/allowed/pending_approval/
// completed/blocked) -- this is meant to read as "this is what the live product
// looks like," not a separately invented look.
const NODE_COLORS = ["#38bdf8", "#22c55e", "#f59e0b", "#ef4444", "#94a3b8"];
const NODE_COUNT = 20;

interface AmbientNode {
  position: [number, number, number];
  color: string;
  radius: number;
}

function randomNodes(count: number): AmbientNode[] {
  const nodes: AmbientNode[] = [];
  for (let i = 0; i < count; i++) {
    const r = 4.5 + Math.random() * 3;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    nodes.push({
      position: [
        r * Math.sin(phi) * Math.cos(theta),
        r * Math.sin(phi) * Math.sin(theta) * 0.55,
        r * Math.cos(phi) - 3,
      ],
      color: NODE_COLORS[Math.floor(Math.random() * NODE_COLORS.length)]!,
      radius: 0.08 + Math.random() * 0.09,
    });
  }
  return nodes;
}

// A handful of edges between nearby nodes, just enough to read as a graph --
// ambient brand texture, not a second real trace with causal meaning.
function buildEdgeLines(nodes: AmbientNode[]): Line[] {
  const lines: Line[] = [];
  for (let i = 0; i < nodes.length; i++) {
    if (Math.random() >= 0.55) continue;
    const j = (i + 1 + Math.floor(Math.random() * 3)) % nodes.length;
    const a = nodes[i]!;
    const b = nodes[j]!;
    const geometry = new BufferGeometry();
    geometry.setAttribute(
      "position",
      new Float32BufferAttribute(new Float32Array([...a.position, ...b.position]), 3),
    );
    lines.push(new Line(geometry, new LineBasicMaterial({ color: "#333a52", transparent: true, opacity: 0.45 })));
  }
  return lines;
}

function reducedMotionPreferred(): boolean {
  return typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}

function Scene() {
  const groupRef = useRef<Group>(null);
  const nodes = useMemo(() => randomNodes(NODE_COUNT), []);
  const edgeLines = useMemo(() => buildEdgeLines(nodes), [nodes]);
  const reducedMotion = useMemo(reducedMotionPreferred, []);

  useFrame((state, delta) => {
    if (reducedMotion || !groupRef.current) return;
    groupRef.current.rotation.y += delta * 0.035;
    groupRef.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.06) * 0.08;
  });

  return (
    <group ref={groupRef}>
      {edgeLines.map((line, i) => (
        <primitive key={i} object={line} />
      ))}
      {nodes.map((node, i) => (
        <mesh key={i} position={node.position}>
          <sphereGeometry args={[node.radius, 12, 12]} />
          <meshStandardMaterial
            color={node.color}
            emissive={node.color}
            emissiveIntensity={0.7}
            roughness={0.4}
          />
        </mesh>
      ))}
    </group>
  );
}

/** Ambient, non-interactive echo of the live execution graph for the
 * login/signup pages -- brand texture (previously a flat static CSS
 * gradient), not a second real graph: positions are randomized decoration,
 * not derived from any actual trace. No OrbitControls (would fight page/
 * form interaction), no pointer events at all, no physics simulation --
 * just a slow autonomous drift, skipped entirely under
 * prefers-reduced-motion rather than just shortened (CLAUDE.md's
 * frontend-design guidance calls for deliberate 3D, not default
 * scaffolding; this reuses GraphCanvas's own lighting/color choices
 * rather than inventing a new palette). */
export function AmbientGraphBackground() {
  return (
    <div className="ambient-graph-bg" aria-hidden="true">
      <Canvas camera={{ position: [0, 0, 9], fov: 50 }} dpr={[1, 1.5]}>
        <ambientLight intensity={0.6} />
        <pointLight position={[5, 5, 5]} intensity={30} />
        <pointLight position={[-5, -3, -5]} intensity={12} color="#38bdf8" />
        <Scene />
      </Canvas>
    </div>
  );
}
