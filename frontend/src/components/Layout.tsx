import type { ReactNode } from "react";
import { Link, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

/** The app chrome for authenticated screens: a header bar + routed content. */
export function Layout(): ReactNode {
  const { session, logout } = useAuth();
  const navigate = useNavigate();

  const onLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="app-shell">
      <header className="app-header">
        <Link to="/" className="app-brand">
          ContextVault
        </Link>
        {session && (
          <div className="app-user">
            <span className="app-username">{session.username}</span>
            <span className="app-role">{session.role}</span>
            <button type="button" onClick={onLogout}>
              Log out
            </button>
          </div>
        )}
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
