import { useState } from "react";
import type { ReactNode } from "react";
import { Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Sidebar } from "./Sidebar";

/** Authenticated app chrome: a left sidebar + routed content. On narrow screens
 *  the sidebar collapses behind a menu toggle. */
export function Layout(): ReactNode {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div className="app-shell" data-nav-open={open ? "true" : "false"}>
      <button
        type="button"
        className="nav-toggle"
        aria-label={t("layout.menu")}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        ☰
      </button>
      <Sidebar />
      <main className="app-main" onClick={() => open && setOpen(false)}>
        <Outlet />
      </main>
    </div>
  );
}
