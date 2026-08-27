import { Navigate, Route, BrowserRouter, Routes } from "react-router-dom";
import { LandingPage } from "./components/LandingPage";
import { LoginPage } from "./components/LoginPage";
import { SignupPage } from "./components/SignupPage";
import { OverviewPage } from "./components/OverviewPage";
import { Dashboard } from "./components/Dashboard";
import { AgentsPage } from "./components/AgentsPage";
import { AgentHealthPage } from "./components/AgentHealthPage";
import { PoliciesPage } from "./components/PoliciesPage";
import { ApprovalsPage } from "./components/ApprovalsPage";
import { TeamPage } from "./components/TeamPage";
import { TracesPage } from "./components/TracesPage";
import { AnalyticsPage } from "./components/AnalyticsPage";
import { ThreatCenterPage } from "./components/ThreatCenterPage";
import { CostCenterPage } from "./components/CostCenterPage";
import { AccountPage } from "./components/AccountPage";
import { DocsPage } from "./components/DocsPage";
import { ToastHost } from "./components/ToastHost";
import { CommandPalette } from "./components/CommandPalette";
import { IncidentReplayPage } from "./components/Replay/IncidentReplayPage";
import { PolicyStudioPage } from "./components/PolicyStudio/PolicyStudioPage";
import { useAuthStore } from "./store/auth";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const accessToken = useAuthStore((s) => s.accessToken);
  if (!accessToken) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

// "/" is the one route that means something different depending on who's
// looking: a public pitch for a first-time visitor (no login wall between
// a judge and "what is this"), the actual dashboard for a signed-in user.
// Kept as a single route (not a redirect) so LoginPage's existing
// navigate("/") on success still lands exactly where it always did.
function HomeRoute() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return accessToken ? <OverviewPage /> : <LandingPage />;
}

export function App() {
  return (
    <BrowserRouter>
      <ToastHost />
      <CommandPalette />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/" element={<HomeRoute />} />
        <Route
          path="/graph"
          element={
            <RequireAuth>
              <Dashboard />
            </RequireAuth>
          }
        />
        <Route
          path="/agents"
          element={
            <RequireAuth>
              <AgentsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/agents/:agentId/health"
          element={
            <RequireAuth>
              <AgentHealthPage />
            </RequireAuth>
          }
        />
        <Route
          path="/threats"
          element={
            <RequireAuth>
              <ThreatCenterPage />
            </RequireAuth>
          }
        />
        <Route
          path="/costs"
          element={
            <RequireAuth>
              <CostCenterPage />
            </RequireAuth>
          }
        />
        <Route
          path="/policies"
          element={
            <RequireAuth>
              <PoliciesPage />
            </RequireAuth>
          }
        />
        <Route
          path="/policy-studio"
          element={
            <RequireAuth>
              <PolicyStudioPage />
            </RequireAuth>
          }
        />
        <Route
          path="/replay/:traceId"
          element={
            <RequireAuth>
              <IncidentReplayPage />
            </RequireAuth>
          }
        />
        <Route
          path="/approvals"
          element={
            <RequireAuth>
              <ApprovalsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/traces"
          element={
            <RequireAuth>
              <TracesPage />
            </RequireAuth>
          }
        />
        <Route
          path="/analytics"
          element={
            <RequireAuth>
              <AnalyticsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/team"
          element={
            <RequireAuth>
              <TeamPage />
            </RequireAuth>
          }
        />
        <Route
          path="/account"
          element={
            <RequireAuth>
              <AccountPage />
            </RequireAuth>
          }
        />
        <Route
          path="/docs"
          element={
            <RequireAuth>
              <DocsPage />
            </RequireAuth>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
