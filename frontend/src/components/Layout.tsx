import type { ReactNode } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/AuthContext";
import { LanguageSwitcher } from "./LanguageSwitcher";

/** The app chrome for authenticated screens: a header bar + routed content. */
export function Layout(): ReactNode {
  const { session, logout } = useAuth();
  const navigate = useNavigate();
  const { t } = useTranslation();

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
        {session?.role === "admin" && (
          <nav className="app-nav">
            <NavLink to="/admin/repositories">{t("nav.repositories")}</NavLink>
            <NavLink to="/admin/sources">{t("nav.sources")}</NavLink>
            <NavLink to="/admin/users">{t("nav.users")}</NavLink>
            <NavLink to="/admin/insights">{t("nav.insights")}</NavLink>
          </nav>
        )}
        <div className="app-user">
          <LanguageSwitcher />
          {session && (
            <>
              <span className="app-username">{session.username}</span>
              <span className="app-role">{session.role}</span>
              <button type="button" onClick={onLogout}>
                {t("layout.logOut")}
              </button>
            </>
          )}
        </div>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
