import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { BufferGeometry, Float32BufferAttribute, Line, LineBasicMaterial } from "three";
import { useSimulationRegistry } from "./SimulationContext";

interface EdgeLineProps {
  from: string;
  to: string;
}

/** Two-point line between two nodes' live simulation positions, updated
 * in place every frame (no React re-render, same reasoning as NodeMesh).
 *
 * Built via `<primitive object={line} />` rather than JSX's `<line>` —
 * React Three Fiber's `line` intrinsic element collides with the DOM's SVG
 * `<line>` in TypeScript's JSX namespace when both are in scope, resolving
 * `ref`/props against the wrong type. `<primitive>` sidesteps it entirely
 * by rendering an object we construct ourselves. */
export function EdgeLine({ from, to }: EdgeLineProps) {
  const { simNodes } = useSimulationRegistry();
  const geometryRef = useRef(new BufferGeometry());
  const line = useMemo(() => {
    geometryRef.current.setAttribute(
      "position",
      new Float32BufferAttribute(new Float32Array(6), 3),
    );
    return new Line(
      geometryRef.current,
      new LineBasicMaterial({ color: "#475569", transparent: true, opacity: 0.55 }),
    );
  }, []);

  useFrame(() => {
    const source = simNodes.get(from);
    const target = simNodes.get(to);
    if (!source || !target) return;
    const position = geometryRef.current.getAttribute("position") as Float32BufferAttribute;
    position.setXYZ(0, source.x ?? 0, source.y ?? 0, source.z ?? 0);
    position.setXYZ(1, target.x ?? 0, target.y ?? 0, target.z ?? 0);
    position.needsUpdate = true;
  });

  return <primitive object={line} />;
}
