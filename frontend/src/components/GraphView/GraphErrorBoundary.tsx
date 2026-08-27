import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** A crash inside the 3D graph (react-three-fiber's own render/commit
 * cycle, or d3-force-3d throwing on malformed edge/node data) previously
 * propagated all the way up and unmounted the *entire* app -- confirmed
 * in production this session: one dangling edge (a parent span that was
 * never itself recorded) threw inside ForceGraph's effect, and the whole
 * #root emptied, not just the Canvas. React only stops a throw from
 * taking down the rest of the tree via a real error boundary — there's
 * no hook-based equivalent, hence the class component. Scoped tightly
 * around just the graph surface so every other page (nav, sidebar,
 * inspector) keeps working even if this one widget breaks again. */
export class GraphErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("GraphErrorBoundary caught a render crash in the live graph:", error, info);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="graph-area__placeholder graph-area__placeholder--error">
          The live graph hit an unexpected error and stopped rendering — the rest of BASTION is
          unaffected. Reconnecting usually clears it.
          <button className="btn btn--ghost" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
