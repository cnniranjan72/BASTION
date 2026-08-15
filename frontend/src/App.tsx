import { Navigate, Route, BrowserRouter, Routes } from "react-router-dom";
import { LoginPage } from "./components/LoginPage";
import { SignupPage } from "./components/SignupPage";
import { OverviewPage } from "./components/OverviewPage";
import { Dashboard } from "./components/Dashboard";
import { AgentsPage } from "./components/AgentsPage";
import { PoliciesPage } from "./components/PoliciesPage";
import { ApprovalsPage } from "./components/ApprovalsPage";
import { TeamPage } from "./components/TeamPage";
import { TracesPage } from "./components/TracesPage";
import { AnalyticsPage } from "./components/AnalyticsPage";
import { AccountPage } from "./components/AccountPage";
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

export function App() {
  return (
    <BrowserRouter>
      <ToastHost />
      <CommandPalette />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <OverviewPage />
            </RequireAuth>
          }
        />
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
      </Routes>
    </BrowserRouter>
  );
}
