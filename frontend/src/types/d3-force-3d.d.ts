// d3-force-3d ships no type declarations and no @types/d3-force-3d package
// exists on npm (checked: 404). This is a deliberately loose ambient
// declaration covering only what GraphCanvas.tsx actually uses — the
// simulation's per-tick node mutation (x/y/z written in place) is
// inherently untyped in d3-force's own design anyway, so a fuller typing
// wouldn't buy much safety here.

declare module "d3-force-3d" {
  export interface SimulationNodeDatum {
    id?: string;
    x?: number;
    y?: number;
    z?: number;
    vx?: number;
    vy?: number;
    vz?: number;
    fx?: number | null;
    fy?: number | null;
    fz?: number | null;
    [key: string]: unknown;
  }

  export interface SimulationLinkDatum<N extends SimulationNodeDatum> {
    source: string | N;
    target: string | N;
  }

  export interface Simulation<N extends SimulationNodeDatum> {
    nodes(): N[];
    nodes(nodes: N[]): this;
    force(name: string): unknown;
    force(name: string, force: unknown): this;
    alpha(): number;
    alpha(alpha: number): this;
    alphaTarget(target: number): this;
    tick(iterations?: number): this;
    stop(): this;
    restart(): this;
  }

  export interface ForceLink<N extends SimulationNodeDatum, L extends SimulationLinkDatum<N>> {
    (alpha: number): void;
    links(): L[];
    links(links: L[]): this;
    id(fn: (node: N) => string): this;
    distance(fn: number | ((link: L) => number)): this;
    strength(fn: number | ((link: L) => number)): this;
  }

  export interface ForceManyBody<N extends SimulationNodeDatum> {
    (alpha: number): void;
    strength(fn: number | ((node: N) => number)): this;
    distanceMin(distance: number): this;
    distanceMax(distance: number): this;
  }

  export interface ForceCenter {
    (alpha: number): void;
    strength(strength: number): this;
  }

  export function forceSimulation<N extends SimulationNodeDatum>(nodes?: N[]): Simulation<N>;
  export function forceManyBody<N extends SimulationNodeDatum>(): ForceManyBody<N>;
  export function forceLink<N extends SimulationNodeDatum, L extends SimulationLinkDatum<N>>(
    links?: L[],
  ): ForceLink<N, L>;
  export function forceCenter(x?: number, y?: number, z?: number): ForceCenter;
}
