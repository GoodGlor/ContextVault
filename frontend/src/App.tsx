import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { RequireAuth, RequireSession } from "./auth/RequireAuth";
import { LoginPage } from "./pages/LoginPage";
import { AcceptInvitePage } from "./pages/AcceptInvitePage";
import { QueryPage } from "./pages/QueryPage";
import { ChangePasswordPage } from "./pages/ChangePasswordPage";
import { AdminRepositoriesPage } from "./pages/AdminRepositoriesPage";
import { AdminProvidersPage } from "./pages/AdminProvidersPage";
import { AdminSourcesPage } from "./pages/AdminSourcesPage";
import { AdminUsersPage } from "./pages/AdminUsersPage";
import { AdminInsightsPage } from "./pages/AdminInsightsPage";

/** Top-level route table. Public auth screens sit outside the protected shell. */
export function App(): ReactNode {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/accept-invite" element={<AcceptInvitePage />} />
      <Route
        path="/change-password"
        element={
          <RequireSession>
            <ChangePasswordPage />
          </RequireSession>
        }
      />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<QueryPage />} />
        <Route
          path="/admin/repositories"
          element={
            <RequireAuth requireAdmin>
              <AdminRepositoriesPage />
            </RequireAuth>
          }
        />
        <Route
          path="/admin/providers"
          element={
            <RequireAuth requireAdmin>
              <AdminProvidersPage />
            </RequireAuth>
          }
        />
        <Route
          path="/admin/sources"
          element={
            <RequireAuth requireAdmin>
              <AdminSourcesPage />
            </RequireAuth>
          }
        />
        <Route
          path="/admin/users"
          element={
            <RequireAuth requireAdmin>
              <AdminUsersPage />
            </RequireAuth>
          }
        />
        <Route
          path="/admin/insights"
          element={
            <RequireAuth requireAdmin>
              <AdminInsightsPage />
            </RequireAuth>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
