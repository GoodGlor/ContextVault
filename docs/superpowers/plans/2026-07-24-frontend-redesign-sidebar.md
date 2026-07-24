# Frontend Redesign — Workspace Sidebar + Unified Data — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat top-header nav with a grouped left sidebar built around one shared repository switcher, and merge the Sources + Database admin pages into one tabbed "Data" page — frontend only.

**Architecture:** A new `RepositoryProvider` holds the single "current repository" (role-based list, localStorage-persisted). A data-driven `Sidebar` renders three nav groups (Workspace / Manage this repo / Admin), admin groups gated on `session.role`. The four repo-scoped pages (Ask, Reports, Data, Insights) drop their own dropdowns and read the context; Providers/Repositories/Users stay global.

**Tech Stack:** React 18 + TypeScript, react-router-dom v6, react-i18next (EN + UK), Vitest + Testing Library. Matches existing conventions: components return `ReactNode`, `useTranslation()` for copy, `ApiError` for API failures.

## Global Constraints

- **Frontend only.** No backend, API-contract, auth, or data-model change. No new npm deps.
- **Behavior preserved.** No screen changes what it *does*; only where it lives and how the repo is selected. Existing behavior tests must stay green (retargeted where a page is extracted/converted).
- **i18n from the start.** Every new/changed UI string added to BOTH `frontend/src/i18n/locales/en.json` and `.../uk.json`. Never hard-code copy.
- **`AdminRepository` is assignable to `Repository`** (it adds only `configured`), so the context types `repos: Repository[]` for both roles.
- **Merged-section label is "Data"** — the single i18n key `nav.data` / `data.title`. Do not invent alternates.
- **Return type** of every component is `ReactNode`; keep the `let cancelled = false` cleanup idiom already used across pages for async effects.
- **Users-grants sub-panel keeps its own repo selector** (different axis — an admin editing any repo's access). Do NOT convert it.

---

### Task 1: RepositoryContext + Provider

**Files:**
- Create: `frontend/src/repository/RepositoryContext.ts`
- Create: `frontend/src/repository/RepositoryProvider.tsx`
- Test: `frontend/src/repository/RepositoryProvider.test.tsx`

**Interfaces:**
- Consumes: `useAuth()` from `../auth/AuthContext` (returns `{ session }`, `session.role` is `"admin" | "member"`); `listRepositories()`/`listAllRepositories()` and `Repository` from `../api/repositories`; `ApiError` from `../api/client`.
- Produces: `RepositoryContext` (React context), `useCurrentRepository(): RepositoryContextValue`, `RepositoryProvider`. `RepositoryContextValue = { repos: Repository[]; currentRepoId: string; setCurrentRepoId: (id: string) => void; loading: boolean; error: string | null }`. `currentRepoId` is `""` when there is no repo.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/repository/RepositoryProvider.test.tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RepositoryProvider } from "./RepositoryProvider";
import { useCurrentRepository } from "./RepositoryContext";

// Auth is faked per-test so we can flip role → which list endpoint is used.
const roleRef = { current: "member" as "member" | "admin" };
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ session: { role: roleRef.current, username: "u", userId: "1" } }),
}));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function Probe() {
  const { repos, currentRepoId, setCurrentRepoId, loading } = useCurrentRepository();
  if (loading) return <p>loading</p>;
  return (
    <div>
      <span data-testid="current">{currentRepoId}</span>
      <span data-testid="count">{repos.length}</span>
      <button onClick={() => setCurrentRepoId("r-2")}>pick2</button>
    </div>
  );
}

describe("RepositoryProvider", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    roleRef.current = "member";
    localStorage.clear();
  });
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("defaults to the first repo and calls the granted-list endpoint for members", async () => {
    fetchMock.mockResolvedValue(json([{ id: "r-1", name: "A" }, { id: "r-2", name: "B" }]));
    render(<RepositoryProvider><Probe /></RepositoryProvider>);
    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("r-1"));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/repositories");
  });

  it("uses the admin all-repos endpoint when the session is admin", async () => {
    roleRef.current = "admin";
    fetchMock.mockResolvedValue(json([{ id: "r-1", name: "A", description: null, configured: true }]));
    render(<RepositoryProvider><Probe /></RepositoryProvider>);
    await waitFor(() => expect(screen.getByTestId("count")).toHaveTextContent("1"));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/repositories");
  });

  it("restores a still-valid stored repo, and persists a new selection", async () => {
    localStorage.setItem("contextvault.currentRepo", "r-2");
    fetchMock.mockResolvedValue(json([{ id: "r-1", name: "A" }, { id: "r-2", name: "B" }]));
    render(<RepositoryProvider><Probe /></RepositoryProvider>);
    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("r-2"));
    await act(() => userEvent.click(screen.getByText("pick2")));
    expect(localStorage.getItem("contextvault.currentRepo")).toBe("r-2");
  });

  it("falls back to the first repo when the stored id is gone", async () => {
    localStorage.setItem("contextvault.currentRepo", "stale");
    fetchMock.mockResolvedValue(json([{ id: "r-1", name: "A" }]));
    render(<RepositoryProvider><Probe /></RepositoryProvider>);
    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("r-1"));
  });
});
```

> Confirm the real endpoint paths before running: check `api/repositories.ts` — `listRepositories()` and `listAllRepositories()`. Use whatever paths they call in the two `expect(...).toBe(...)` endpoint assertions (`/api/repositories` and `/api/admin/repositories` are the expected values; correct them if the source differs).

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm run test -- src/repository/RepositoryProvider.test.tsx`
Expected: FAIL — modules `./RepositoryProvider` / `./RepositoryContext` do not exist.

- [ ] **Step 3: Create the context module**

```ts
// frontend/src/repository/RepositoryContext.ts
import { createContext, useContext } from "react";
import type { Repository } from "../api/repositories";

export interface RepositoryContextValue {
  /** Accessible repositories for the current role (granted for members, all for admins). */
  repos: Repository[];
  /** The repo every repo-scoped page reads; "" when there is none. */
  currentRepoId: string;
  setCurrentRepoId: (id: string) => void;
  loading: boolean;
  error: string | null;
}

export const RepositoryContext = createContext<RepositoryContextValue | null>(null);

/** Read the shared current-repository state. Throws if used outside the provider. */
export function useCurrentRepository(): RepositoryContextValue {
  const ctx = useContext(RepositoryContext);
  if (ctx === null) {
    throw new Error("useCurrentRepository must be used within a RepositoryProvider");
  }
  return ctx;
}
```

- [ ] **Step 4: Create the provider**

```tsx
// frontend/src/repository/RepositoryProvider.tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../api/client";
import { listAllRepositories, listRepositories, type Repository } from "../api/repositories";
import { useAuth } from "../auth/AuthContext";
import { RepositoryContext, type RepositoryContextValue } from "./RepositoryContext";

const STORAGE_KEY = "contextvault.currentRepo";

function readStored(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}
function writeStored(id: string): void {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* localStorage may be unavailable; selection just won't persist */
  }
}

/** Owns the single "current repository" for the app. The sidebar switcher writes
 *  it; repo-scoped pages read it via useCurrentRepository(). Admins see all
 *  repositories, members only granted ones. */
export function RepositoryProvider({ children }: { children: ReactNode }): ReactNode {
  const { t } = useTranslation();
  const { session } = useAuth();
  const isAdmin = session?.role === "admin";

  const [repos, setRepos] = useState<Repository[]>([]);
  const [currentRepoId, setCurrent] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const load = isAdmin ? listAllRepositories() : listRepositories();
    load
      .then((rs) => {
        if (cancelled) return;
        setRepos(rs);
        const stored = readStored();
        const next = rs.some((r) => r.id === stored) ? stored : (rs[0]?.id ?? "");
        setCurrent(next);
        writeStored(next);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.detail : t("repository.errorLoad"));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isAdmin, t]);

  const setCurrentRepoId = useCallback((id: string) => {
    setCurrent(id);
    writeStored(id);
  }, []);

  const value = useMemo<RepositoryContextValue>(
    () => ({ repos, currentRepoId, setCurrentRepoId, loading, error }),
    [repos, currentRepoId, setCurrentRepoId, loading, error],
  );

  return <RepositoryContext.Provider value={value}>{children}</RepositoryContext.Provider>;
}
```

- [ ] **Step 5: Add the i18n key**

Add `"repository": { "errorLoad": "..." }` to both locale files (EN: `"Could not load your repositories."`, UK: `"Не вдалося завантажити репозиторії."`).

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd frontend && npm run test -- src/repository/RepositoryProvider.test.tsx`
Expected: PASS (4/4).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/repository frontend/src/i18n/locales
git commit -m "feat(fe): shared RepositoryContext + provider (current-repo state)"
```

---

### Task 2: Sidebar component

**Files:**
- Create: `frontend/src/components/Sidebar.tsx`
- Test: `frontend/src/components/Sidebar.test.tsx`

**Interfaces:**
- Consumes: `useCurrentRepository()` (Task 1); `useAuth()` (`session.role`, `session.username`, `logout`); `NavLink` from `react-router-dom`; existing `LanguageSwitcher` from `./LanguageSwitcher`.
- Produces: `Sidebar` (default nav chrome). Nav is data-driven from a local `NAV` config.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/Sidebar.test.tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { RepositoryContext, type RepositoryContextValue } from "../repository/RepositoryContext";

const roleRef = { current: "member" as "member" | "admin" };
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ session: { role: roleRef.current, username: "artem", userId: "1" }, logout: vi.fn() }),
}));

const repoValue: RepositoryContextValue = {
  repos: [{ id: "r-1", name: "Kyiv Support", description: null }],
  currentRepoId: "r-1",
  setCurrentRepoId: vi.fn(),
  loading: false,
  error: null,
};

function renderSidebar() {
  return render(
    <MemoryRouter>
      <RepositoryContext.Provider value={repoValue}>
        <Sidebar />
      </RepositoryContext.Provider>
    </MemoryRouter>,
  );
}

describe("Sidebar", () => {
  beforeEach(() => (roleRef.current = "member"));
  afterEach(() => vi.restoreAllMocks());

  it("shows only workspace links for members", () => {
    renderSidebar();
    expect(screen.getByRole("link", { name: /ask/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /reports/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /data/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /users/i })).not.toBeInTheDocument();
  });

  it("shows manage + admin groups (incl. Data) for admins", () => {
    roleRef.current = "admin";
    renderSidebar();
    expect(screen.getByRole("link", { name: /data/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /providers/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /users/i })).toBeInTheDocument();
    // no separate Sources / Database links anymore
    expect(screen.queryByRole("link", { name: /^sources$/i })).not.toBeInTheDocument();
  });

  it("renders the repository switcher bound to context", () => {
    renderSidebar();
    expect(screen.getByRole("combobox", { name: /repository/i })).toHaveValue("r-1");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test -- src/components/Sidebar.test.tsx`
Expected: FAIL — `./Sidebar` does not exist.

- [ ] **Step 3: Implement the Sidebar**

```tsx
// frontend/src/components/Sidebar.tsx
import type { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/AuthContext";
import { useCurrentRepository } from "../repository/RepositoryContext";
import { LanguageSwitcher } from "./LanguageSwitcher";

interface NavItemDef {
  to: string;
  labelKey: string;
  icon: string;
  end?: boolean;
}
interface NavGroupDef {
  labelKey: string;
  adminOnly: boolean;
  items: NavItemDef[];
}

/** Navigation model. Groups render top-to-bottom; admin-only groups are hidden
 *  for members. Editing nav = editing this array. */
const NAV: NavGroupDef[] = [
  {
    labelKey: "nav.groupWorkspace",
    adminOnly: false,
    items: [
      { to: "/", labelKey: "nav.query", icon: "💬", end: true },
      { to: "/reports", labelKey: "nav.reports", icon: "📊" },
    ],
  },
  {
    labelKey: "nav.groupManage",
    adminOnly: true,
    items: [
      { to: "/admin/data", labelKey: "nav.data", icon: "🧠" },
      { to: "/admin/providers", labelKey: "nav.providers", icon: "🔌" },
      { to: "/admin/insights", labelKey: "nav.insights", icon: "📈" },
    ],
  },
  {
    labelKey: "nav.groupAdmin",
    adminOnly: true,
    items: [
      { to: "/admin/repositories", labelKey: "nav.repositories", icon: "📁" },
      { to: "/admin/users", labelKey: "nav.users", icon: "👥" },
    ],
  },
];

export function Sidebar(): ReactNode {
  const { t } = useTranslation();
  const { session, logout } = useAuth();
  const navigate = useNavigate();
  const { repos, currentRepoId, setCurrentRepoId } = useCurrentRepository();
  const isAdmin = session?.role === "admin";

  const onLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">ContextVault</div>

      {repos.length > 0 && (
        <label className="repo-switch">
          <span className="repo-switch-caption">{t("nav.repository")}</span>
          <select
            aria-label={t("nav.repository")}
            value={currentRepoId}
            onChange={(e) => setCurrentRepoId(e.target.value)}
          >
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
      )}

      <nav className="sidebar-nav">
        {NAV.filter((g) => !g.adminOnly || isAdmin).map((group) => (
          <div key={group.labelKey} className="nav-group">
            <span className="nav-group-label">{t(group.labelKey)}</span>
            {group.items.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end} className="nav-item">
                <span className="nav-ico" aria-hidden="true">
                  {item.icon}
                </span>
                {t(item.labelKey)}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar-foot">
        <LanguageSwitcher />
        {session && (
          <div className="sidebar-user">
            <span className="sidebar-username">{session.username}</span>
            <span className="sidebar-role">{session.role}</span>
            <button type="button" onClick={onLogout}>
              {t("layout.logOut")}
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
```

- [ ] **Step 4: Add i18n keys**

Add to BOTH locales under `nav`: `groupWorkspace`, `groupManage`, `groupAdmin`, `data`, `repository`.
- EN: `"groupWorkspace": "Workspace"`, `"groupManage": "Manage this repo"`, `"groupAdmin": "Admin"`, `"data": "Data"`, `"repository": "Repository"`.
- UK: `"groupWorkspace": "Робочий простір"`, `"groupManage": "Керування репозиторієм"`, `"groupAdmin": "Адміністрування"`, `"data": "Дані"`, `"repository": "Репозиторій"`.

- [ ] **Step 5: Run to verify it passes**

Run: `cd frontend && npm run test -- src/components/Sidebar.test.tsx`
Expected: PASS (3/3).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/components/Sidebar.test.tsx frontend/src/i18n/locales
git commit -m "feat(fe): data-driven grouped Sidebar with repo switcher"
```

---

### Task 3: Layout shell + provider wiring + sidebar CSS

**Files:**
- Modify: `frontend/src/components/Layout.tsx` (replace header with sidebar shell)
- Modify: `frontend/src/App.tsx` (wrap protected subtree in `RepositoryProvider`)
- Modify: `frontend/src/index.css` (sidebar + responsive styles; retire header-only rules)

**Interfaces:**
- Consumes: `Sidebar` (Task 2), `RepositoryProvider` (Task 1), `Outlet` from `react-router-dom`.
- Produces: two-column app shell (`.app-shell` = sidebar + `<main class="app-main">`). Provider wraps every authenticated route so `useCurrentRepository()` is always available under `Layout`.

- [ ] **Step 1: Replace Layout body**

```tsx
// frontend/src/components/Layout.tsx
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
```

Add i18n key `layout.menu` (EN `"Menu"`, UK `"Меню"`).

- [ ] **Step 2: Wrap the protected routes in the provider**

In `App.tsx`, import `RepositoryProvider` and wrap `<Layout />`:

```tsx
import { RepositoryProvider } from "./repository/RepositoryProvider";
// ...
<Route
  element={
    <RequireAuth>
      <RepositoryProvider>
        <Layout />
      </RepositoryProvider>
    </RequireAuth>
  }
>
```

Leave all child routes unchanged in this task (Data routes come in Task 6b).

- [ ] **Step 3: Sidebar CSS**

Replace the `/* ---- header + nav ---- */` block in `index.css` with the sidebar system. Reuse the existing tokens (`--surface-2`, `--accent-soft`, `--border`, etc.). Key rules:

```css
.app-shell { display: grid; grid-template-columns: 244px 1fr; min-height: 100vh; }
.nav-toggle { display: none; }
.sidebar {
  display: flex; flex-direction: column; gap: 6px;
  padding: 16px 12px; border-right: 1px solid var(--border);
  background: rgba(15, 18, 24, 0.6); backdrop-filter: blur(12px);
  position: sticky; top: 0; height: 100vh; overflow-y: auto;
}
.sidebar-brand {
  font-weight: 750; font-size: 1.05rem; letter-spacing: -0.02em; padding: 6px 8px 10px;
  background: linear-gradient(135deg, var(--text), #a9b6ff);
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
}
.repo-switch { display: block; margin-bottom: 8px; }
.repo-switch-caption {
  display: block; font-size: 0.64rem; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted); margin: 0 0 4px 2px;
}
.repo-switch select { margin-top: 0; }
.nav-group { display: flex; flex-direction: column; gap: 2px; margin-top: 12px; }
.nav-group-label {
  font-size: 0.64rem; font-weight: 700; letter-spacing: 0.11em; text-transform: uppercase;
  color: var(--muted); padding: 0 8px; margin-bottom: 4px;
}
.nav-item {
  display: flex; align-items: center; gap: 9px;
  font-size: 0.9rem; font-weight: 600; color: var(--text-dim);
  padding: 8px 10px; border-radius: var(--radius-sm);
}
.nav-item:hover { background: var(--surface-hover); color: var(--text); }
.nav-item.active { background: var(--accent-soft); color: var(--text); }
.nav-ico { width: 18px; text-align: center; }
.sidebar-foot { margin-top: auto; padding-top: 12px; border-top: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; }
.sidebar-user { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 0.82rem; color: var(--muted); }
.sidebar-role { text-transform: capitalize; color: var(--accent); }
.app-main { padding: 24px 28px; min-width: 0; }

@media (max-width: 860px) {
  .app-shell { grid-template-columns: 1fr; }
  .nav-toggle {
    display: inline-flex; position: fixed; top: 12px; left: 12px; z-index: 30;
    width: 40px; height: 40px; align-items: center; justify-content: center;
  }
  .sidebar {
    position: fixed; z-index: 25; width: 244px; height: 100vh;
    transform: translateX(-100%); transition: transform 0.2s ease;
  }
  .app-shell[data-nav-open="true"] .sidebar { transform: translateX(0); }
  .app-main { padding: 60px 18px 24px; }
}
@media (prefers-reduced-motion: reduce) { .sidebar { transition: none; } }
```

Remove now-dead selectors: `.app-header`, `.app-brand`, `.app-nav`, `.app-user`, `.app-username`, `.app-role` (grep first; delete only rules with no remaining references).

- [ ] **Step 4: Verify build + full suite**

Run: `cd frontend && npm run typecheck && npm run test`
Expected: PASS. (Existing page tests that render a page inside `<Layout>` via router should still pass; if any test asserted on the old header, retarget it minimally to the sidebar.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Layout.tsx frontend/src/App.tsx frontend/src/index.css frontend/src/i18n/locales
git commit -m "feat(fe): sidebar app shell + RepositoryProvider wiring"
```

---

### Task 4: QueryPage reads the shared repository

**Files:**
- Modify: `frontend/src/pages/QueryPage.tsx`
- Modify: `frontend/src/pages/QueryPage.test.tsx`
- Create: `frontend/src/test/renderWithRepo.tsx` (shared test helper)

**Interfaces:**
- Consumes: `useCurrentRepository()` — replaces the local `repos`/`selected`/repo-list effect and the in-chat `<select>`.
- Produces: unchanged public component `QueryPage`. Behavior identical except the repo now comes from context; the per-repo conversation reload keys on `currentRepoId`.

- [ ] **Step 1: Add the shared test helper**

```tsx
// frontend/src/test/renderWithRepo.tsx
import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { RepositoryContext, type RepositoryContextValue } from "../repository/RepositoryContext";

export function repoValue(overrides: Partial<RepositoryContextValue> = {}): RepositoryContextValue {
  return {
    repos: [{ id: "r-1", name: "Handbook", description: null }],
    currentRepoId: "r-1",
    setCurrentRepoId: () => {},
    loading: false,
    error: null,
    ...overrides,
  };
}

/** Render a repo-scoped page/panel inside a seeded RepositoryContext + router. */
export function renderWithRepo(ui: ReactElement, value: RepositoryContextValue = repoValue()) {
  return render(
    <MemoryRouter>
      <RepositoryContext.Provider value={value}>{ui}</RepositoryContext.Provider>
    </MemoryRouter>,
  );
}
```

- [ ] **Step 2: Update QueryPage.test to seed context (write the failing test)**

Rewrite the test to render via `renderWithRepo(<QueryPage />)` instead of the bare page, and DELETE the fetch mock that returned the repository list plus any interaction with the in-page repo `<select>` (that dropdown is gone). Keep every assertion about asking a question, citations, conversation reload, and clear-conversation — those behaviors are unchanged. Example of the new top:

```tsx
import { renderWithRepo, repoValue } from "../test/renderWithRepo";
// ...
it("asks a question and renders the cited answer", async () => {
  fetchMock
    .mockResolvedValueOnce(json({ turns: [] }))            // getConversation on mount
    .mockResolvedValueOnce(json({ answer: "42", not_in_vault: false, citations: [], sources: [] })); // queryRepository
  renderWithRepo(<QueryPage />);
  // ...unchanged assertions...
});
```

> The exact mock sequence depends on the current test; preserve its intent. The one structural change: the page no longer fetches the repo list, so drop that first `listRepositories` mock and get the repo from `repoValue()`.

- [ ] **Step 3: Run to verify it fails**

Run: `cd frontend && npm run test -- src/pages/QueryPage.test.tsx`
Expected: FAIL — `QueryPage` still calls `listRepositories` / renders a `<select>` the new test doesn't provide/expect.

- [ ] **Step 4: Convert QueryPage**

- Remove imports of `listRepositories` and the local `repos`, `reposError`, `selected` state and the repo-list `useEffect`.
- Add `import { useCurrentRepository } from "../repository/RepositoryContext";` and, at the top of the component: `const { repos, currentRepoId, loading, error } = useCurrentRepository();`
- Replace every `selected` with `currentRepoId` (the conversation-reload effect, `submit`, `onClear`, `onSelectRepo` is removed).
- Replace the guard clauses:

```tsx
if (error !== null) return <div className="page"><p className="form-error" role="alert">{error}</p></div>;
if (loading) return <div className="page"><p>{t("query.loadingRepos")}</p></div>;
if (repos.length === 0) return <div className="page"><h1>{t("query.title")}</h1><p>{t("query.noAccess")}</p></div>;
```

- Delete the `.chat-repo` `<label>`/`<select>` block from `.chat-header` (the switcher now lives in the sidebar). Keep the `<h1>` and the clear-conversation button.

- [ ] **Step 5: Run to verify it passes**

Run: `cd frontend && npm run test -- src/pages/QueryPage.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/QueryPage.tsx frontend/src/pages/QueryPage.test.tsx frontend/src/test/renderWithRepo.tsx
git commit -m "refactor(fe): QueryPage reads shared repository context"
```

---

### Task 5: ReportsPage reads the shared repository

**Files:**
- Modify: `frontend/src/pages/ReportsPage.tsx`
- Modify: `frontend/src/pages/ReportsPage.test.tsx`

**Interfaces:**
- Consumes: `useCurrentRepository()` and `renderWithRepo` helper (Task 4).
- Produces: unchanged `ReportsPage`; repo from context; report/schedule reload + poll key on `currentRepoId`.

- [ ] **Step 1: Update ReportsPage.test to seed context (failing test)**

Render via `renderWithRepo(<ReportsPage />)`; drop the `listRepositories` mock and the repo `<select>` interaction; keep all report-generation, polling, download, schedule assertions.

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test -- src/pages/ReportsPage.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Convert ReportsPage**

Apply the identical transformation as Task 4:
- Remove `listRepositories` import, local `repos`/`reposError`/`selected` state, and the repo-list effect.
- Add `const { repos, currentRepoId, loading, error } = useCurrentRepository();`
- Replace `selected` → `currentRepoId` throughout (`useEffect` deps, `onSubmit`, `onDownload`, `onRepeatNightly`).
- Replace guards:

```tsx
if (error !== null) return <p className="error">{error}</p>;
if (loading) return <p>{t("reports.loadingRepos")}</p>;
if (repos.length === 0) return <p>{t("reports.noRepos")}</p>;
```

- Delete the `report-repo` `<label>`/`<select>` block; keep everything else.

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npm run test -- src/pages/ReportsPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ReportsPage.tsx frontend/src/pages/ReportsPage.test.tsx
git commit -m "refactor(fe): ReportsPage reads shared repository context"
```

---

### Task 6a: Extract SourcesPanel and DatabasePanel

**Files:**
- Create: `frontend/src/pages/data/SourcesPanel.tsx`
- Create: `frontend/src/pages/data/DatabasePanel.tsx`
- Create: `frontend/src/pages/data/SourcesPanel.test.tsx` (moved from `AdminSourcesPage.test.tsx`)
- Create: `frontend/src/pages/data/DatabasePanel.test.tsx` (moved from `AdminDatabasePage.test.tsx`)
- Delete: `frontend/src/pages/AdminSourcesPage.tsx`, `frontend/src/pages/AdminSourcesPage.test.tsx`, `frontend/src/pages/AdminDatabasePage.tsx`, `frontend/src/pages/AdminDatabasePage.test.tsx`

**Interfaces:**
- Consumes: `useCurrentRepository()` (uses `currentRepoId` in place of each page's old `selected`).
- Produces: `SourcesPanel` and `DatabasePanel` — the exact bodies of the two admin pages with repo selection removed. `SOURCE_POLL_MS` re-exported from `SourcesPanel`. Behavior identical.

- [ ] **Step 1: Create SourcesPanel from AdminSourcesPage**

Copy `AdminSourcesPage.tsx` to `data/SourcesPanel.tsx` and apply exactly these edits (nothing else changes):
- Rename the export: `export function SourcesPanel(): ReactNode`.
- Remove `import { listAllRepositories, type AdminRepository } from "../api/repositories";` → replace with `import { useCurrentRepository } from "../../repository/RepositoryContext";` (note the `../../` — deeper folder). Fix the other relative imports to `../../` (`../api/...` → `../../api/...`).
- Delete the `repos`, `reposError`, `selected` state and the repo-list `useEffect`. Add at top: `const { currentRepoId } = useCurrentRepository();`
- Replace every `selected` with `currentRepoId`.
- Delete the three guard clauses that referenced `reposError`/`repos` and the repo `<label>/<select>` block. Add one guard: `if (currentRepoId === "") return <p>{t("adminSources.noRepos")}</p>;`
- Remove the outer `<h1>{t("adminSources.title")}</h1>` (the Data page owns the title); keep the `<section className="admin-sources">` wrapper and all forms/lists.
- Keep `export const SOURCE_POLL_MS = 2000;`.

Result is a behavior-identical panel. The full expected file:

```tsx
// frontend/src/pages/data/SourcesPanel.tsx
import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../../api/client";
import { useCurrentRepository } from "../../repository/RepositoryContext";
import {
  addWebSource,
  deleteSource,
  isIngesting,
  listSources,
  uploadSource,
  type Source,
} from "../../api/sources";

export const SOURCE_POLL_MS = 2000;

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

/** Documents & web sources for the current repository: upload, watch ingestion,
 *  delete. Repo comes from the shared switcher. */
export function SourcesPanel(): ReactNode {
  const { t } = useTranslation();
  const { currentRepoId } = useCurrentRepository();

  const [sources, setSources] = useState<Source[] | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [webUrl, setWebUrl] = useState("");
  const [addingWeb, setAddingWeb] = useState(false);
  const [webError, setWebError] = useState<string | null>(null);

  useEffect(() => {
    if (currentRepoId === "") return;
    let cancelled = false;
    setSources(null);
    setSourcesError(null);
    listSources(currentRepoId)
      .then((s) => !cancelled && setSources(s))
      .catch(
        (err: unknown) =>
          !cancelled && setSourcesError(errorMessage(err, t("adminSources.errorLoadSources"))),
      );
    return () => {
      cancelled = true;
    };
  }, [currentRepoId, t]);

  useEffect(() => {
    if (currentRepoId === "" || sources === null) return;
    if (!sources.some((s) => isIngesting(s.status))) return;
    const timer = setTimeout(() => {
      listSources(currentRepoId)
        .then(setSources)
        .catch(() => {
          /* transient; the next tick retries */
        });
    }, SOURCE_POLL_MS);
    return () => clearTimeout(timer);
  }, [sources, currentRepoId]);

  const onFileChange = (e: ChangeEvent<HTMLInputElement>) => setFiles(Array.from(e.target.files ?? []));

  const onUpload = async (e: FormEvent) => {
    e.preventDefault();
    if (currentRepoId === "" || files.length === 0) return;
    setUploading(true);
    setUploadError(null);
    const results = await Promise.allSettled(files.map((f) => uploadSource(currentRepoId, f)));
    const created = results
      .filter((r): r is PromiseFulfilledResult<Source> => r.status === "fulfilled")
      .map((r) => r.value);
    if (created.length > 0) setSources((prev) => [...(prev ?? []), ...created]);
    const failed = results.length - created.length;
    if (failed > 0) {
      const firstRejected = results.find((r) => r.status === "rejected") as
        | PromiseRejectedResult
        | undefined;
      const detail =
        created.length === 0 && firstRejected?.reason instanceof ApiError
          ? firstRejected.reason.detail
          : null;
      setUploadError(detail ?? t("adminSources.errorUploadSome", { failed, total: results.length }));
    } else {
      setFiles([]);
      if (fileInput.current) fileInput.current.value = "";
    }
    setUploading(false);
  };

  const onAddWeb = async (e: FormEvent) => {
    e.preventDefault();
    if (currentRepoId === "" || webUrl.trim() === "") return;
    setAddingWeb(true);
    setWebError(null);
    try {
      const created = await addWebSource(currentRepoId, webUrl.trim());
      setSources((prev) => [...(prev ?? []), created]);
      setWebUrl("");
    } catch (err) {
      setWebError(errorMessage(err, t("adminSources.errorAddLink")));
    } finally {
      setAddingWeb(false);
    }
  };

  const onDelete = async (id: string) => {
    try {
      await deleteSource(id);
      setSources((prev) => prev?.filter((s) => s.id !== id) ?? prev);
    } catch (err) {
      setSourcesError(errorMessage(err, t("adminSources.errorDelete")));
    }
  };

  if (currentRepoId === "") return <p>{t("adminSources.noRepos")}</p>;

  const statusLabels: Record<Source["status"], string> = {
    pending: t("adminSources.statusPending"),
    processing: t("adminSources.statusProcessing"),
    done: t("adminSources.statusDone"),
    failed: t("adminSources.statusFailed"),
  };

  return (
    <section className="admin-sources">
      <form className="source-upload" onSubmit={onUpload}>
        <label htmlFor="source-file">{t("adminSources.documentLabel")}</label>
        <input
          id="source-file"
          type="file"
          multiple
          ref={fileInput}
          onChange={onFileChange}
          accept=".txt,.pdf,.docx,.png,.jpg,.jpeg,.webp,.tiff,.bmp,.heic,.heif"
        />
        <p className="form-hint">{t("adminSources.ocrHint")}</p>
        <button type="submit" disabled={uploading || files.length === 0}>
          {files.length > 1
            ? t("adminSources.uploadButtonCount", { n: files.length })
            : t("adminSources.uploadButton")}
        </button>
        {uploadError !== null && <p className="error">{uploadError}</p>}
      </form>

      <form className="source-web" onSubmit={onAddWeb}>
        <label htmlFor="source-url">{t("adminSources.webLinkLabel")}</label>
        <input
          id="source-url"
          type="url"
          placeholder={t("adminSources.urlPlaceholder")}
          value={webUrl}
          onChange={(e) => setWebUrl(e.target.value)}
        />
        <button type="submit" disabled={addingWeb || webUrl.trim() === ""}>
          {t("adminSources.addLinkButton")}
        </button>
        {webError !== null && <p className="error">{webError}</p>}
      </form>

      {sourcesError !== null && <p className="error">{sourcesError}</p>}
      {sources === null ? (
        <p>{t("adminSources.loadingSources")}</p>
      ) : sources.length === 0 ? (
        <p>{t("adminSources.noSources")}</p>
      ) : (
        <ul className="source-list">
          {sources.map((s) => (
            <li key={s.id} className="source-item">
              <span className={`badge kind-${s.kind}`}>{s.kind}</span>
              {s.kind === "web" && s.source_url !== null ? (
                <a className="source-title" href={s.source_url} target="_blank" rel="noreferrer">
                  {s.title}
                </a>
              ) : (
                <span className="source-title">{s.title}</span>
              )}
              <span className={`badge status-${s.status}`}>{statusLabels[s.status]}</span>
              {s.status === "failed" && s.ingest_error !== null && (
                <span className="source-error">{s.ingest_error}</span>
              )}
              <button type="button" onClick={() => onDelete(s.id)}>
                {t("adminSources.deleteButton")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
```

- [ ] **Step 2: Create DatabasePanel from AdminDatabasePage**

Read `frontend/src/pages/AdminDatabasePage.tsx` in full and apply the SAME transformation recipe as Step 1 (it uses the identical repo-selection idiom: `listAllRepositories`, `repos`/`selected` state, a repo-loading effect, and a repo `<select>`):
- Copy to `data/DatabasePanel.tsx`, export `function DatabasePanel(): ReactNode`.
- Fix relative imports to `../../`.
- Drop `listAllRepositories`/`AdminRepository` import, the `repos`/`reposError`/`selected` state, and the repo-list effect; add `const { currentRepoId } = useCurrentRepository();`.
- Replace `selected` → `currentRepoId`; replace repo guards with `if (currentRepoId === "") return <p>{t("adminDatabase.noRepos")}</p>;` (use the page's actual "no repos" key — grep the file).
- Remove the page `<h1>`; keep the rest verbatim.

- [ ] **Step 3: Move the two test files (write the failing tests)**

- Move `AdminSourcesPage.test.tsx` → `data/SourcesPanel.test.tsx`: import `SourcesPanel, SOURCE_POLL_MS` from `./SourcesPanel`; render via a local `renderWithRepo` (import from `../../test/renderWithRepo`); delete the `listAllRepositories` mock (first fetch) and any repo `<select>` interaction; keep all upload / web / ingestion-poll / delete assertions. Seed `repoValue({ repos: [{ id: "r-1", name: "Handbook", description: null }] })`.
- Move `AdminDatabasePage.test.tsx` → `data/DatabasePanel.test.tsx` the same way.
- Delete the four old files.

- [ ] **Step 4: Run to verify the panels pass**

Run: `cd frontend && npm run test -- src/pages/data`
Expected: PASS — all migrated Sources/Database assertions green against the panels.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/data
git rm frontend/src/pages/AdminSourcesPage.tsx frontend/src/pages/AdminSourcesPage.test.tsx frontend/src/pages/AdminDatabasePage.tsx frontend/src/pages/AdminDatabasePage.test.tsx
git commit -m "refactor(fe): extract SourcesPanel + DatabasePanel from admin pages"
```

---

### Task 6b: AdminDataPage (tabs) + routing/redirects

**Files:**
- Create: `frontend/src/pages/AdminDataPage.tsx`
- Create: `frontend/src/pages/AdminDataPage.test.tsx`
- Modify: `frontend/src/App.tsx` (add `/admin/data`, redirect `/admin/sources` + `/admin/database`, drop deleted imports)
- Modify: `frontend/src/index.css` (tabbar styles)

**Interfaces:**
- Consumes: `SourcesPanel`, `DatabasePanel` (Task 6a); `useSearchParams` from `react-router-dom`.
- Produces: `AdminDataPage` — a titled page with two tabs (`?tab=documents|database`, default `documents`).

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/pages/AdminDataPage.test.tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AdminDataPage } from "./AdminDataPage";
import { RepositoryContext, type RepositoryContextValue } from "../repository/RepositoryContext";

const repoValue: RepositoryContextValue = {
  repos: [{ id: "r-1", name: "Handbook", description: null }],
  currentRepoId: "r-1",
  setCurrentRepoId: vi.fn(),
  loading: false,
  error: null,
};

function renderData(initial = "/admin/data") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <RepositoryContext.Provider value={repoValue}>
        <AdminDataPage />
      </RepositoryContext.Provider>
    </MemoryRouter>,
  );
}

describe("AdminDataPage", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValue(new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("shows the Documents tab selected by default", () => {
    renderData();
    expect(screen.getByRole("tab", { name: /documents/i })).toHaveAttribute("aria-selected", "true");
  });

  it("honors ?tab=database on load", () => {
    renderData("/admin/data?tab=database");
    expect(screen.getByRole("tab", { name: /database/i })).toHaveAttribute("aria-selected", "true");
  });

  it("switches tabs on click", async () => {
    renderData();
    await userEvent.click(screen.getByRole("tab", { name: /database/i }));
    expect(screen.getByRole("tab", { name: /database/i })).toHaveAttribute("aria-selected", "true");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test -- src/pages/AdminDataPage.test.tsx`
Expected: FAIL — `./AdminDataPage` does not exist.

- [ ] **Step 3: Implement AdminDataPage**

```tsx
// frontend/src/pages/AdminDataPage.tsx
import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { SourcesPanel } from "./data/SourcesPanel";
import { DatabasePanel } from "./data/DatabasePanel";

type Tab = "documents" | "database";

/** One "Data" surface for the current repository, merging the former Sources and
 *  Database admin pages into two tabs. Active tab is reflected in ?tab= so it is
 *  linkable and survives reload. */
export function AdminDataPage(): ReactNode {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "database" ? "database" : "documents";

  const select = (next: Tab) => {
    const p = new URLSearchParams(params);
    p.set("tab", next);
    setParams(p, { replace: true });
  };

  return (
    <section className="admin-data page">
      <h1>{t("data.title")}</h1>
      <div className="tabbar" role="tablist" aria-label={t("data.title")}>
        <button
          type="button"
          role="tab"
          id="tab-documents"
          aria-controls="panel-documents"
          aria-selected={tab === "documents"}
          className={tab === "documents" ? "tab active" : "tab"}
          onClick={() => select("documents")}
        >
          {t("data.tabDocuments")}
        </button>
        <button
          type="button"
          role="tab"
          id="tab-database"
          aria-controls="panel-database"
          aria-selected={tab === "database"}
          className={tab === "database" ? "tab active" : "tab"}
          onClick={() => select("database")}
        >
          {t("data.tabDatabase")}
        </button>
      </div>

      {tab === "documents" ? (
        <div role="tabpanel" id="panel-documents" aria-labelledby="tab-documents">
          <SourcesPanel />
        </div>
      ) : (
        <div role="tabpanel" id="panel-database" aria-labelledby="tab-database">
          <DatabasePanel />
        </div>
      )}
    </section>
  );
}
```

Add i18n keys `data.title` (EN `"Data"`, UK `"Дані"`), `data.tabDocuments` (EN `"Documents & web"`, UK `"Документи та веб"`), `data.tabDatabase` (EN `"Database"`, UK `"База даних"`).

- [ ] **Step 4: Wire routes + redirects in App.tsx**

- Remove imports of `AdminSourcesPage`, `AdminDatabasePage`; import `AdminDataPage` and ensure `Navigate` is imported (it already is).
- Replace the `/admin/sources` and `/admin/database` route elements with redirects and add the new page:

```tsx
<Route path="/admin/data" element={<RequireAuth requireAdmin><AdminDataPage /></RequireAuth>} />
<Route path="/admin/sources" element={<Navigate to="/admin/data?tab=documents" replace />} />
<Route path="/admin/database" element={<Navigate to="/admin/data?tab=database" replace />} />
```

- [ ] **Step 5: Tabbar CSS** (append to `index.css`)

```css
.tabbar { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin: 0 0 20px; }
.tabbar .tab {
  border: 0; background: transparent; color: var(--muted); font-weight: 650;
  padding: 9px 14px; border-bottom: 2px solid transparent; border-radius: 0; cursor: pointer;
}
.tabbar .tab:hover { color: var(--text); }
.tabbar .tab.active { color: var(--text); border-bottom-color: var(--accent); }
.tabbar .tab:focus-visible { outline: none; box-shadow: 0 0 0 3px var(--accent-ring); }
```

- [ ] **Step 6: Run tests + typecheck**

Run: `cd frontend && npm run typecheck && npm run test -- src/pages/AdminDataPage.test.tsx`
Expected: PASS (3/3), no type errors (deleted-page imports gone).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/AdminDataPage.tsx frontend/src/pages/AdminDataPage.test.tsx frontend/src/App.tsx frontend/src/index.css frontend/src/i18n/locales
git commit -m "feat(fe): unified Data page (Documents + Database tabs) + redirects"
```

---

### Task 7: AdminInsightsPage reads the shared repository

**Files:**
- Modify: `frontend/src/pages/AdminInsightsPage.tsx`
- Modify: `frontend/src/pages/AdminInsightsPage.test.tsx`

**Interfaces:**
- Consumes: `useCurrentRepository()`.
- Produces: unchanged `AdminInsightsPage`; the `KnowledgeGapsPanel` now follows the global repo. `AnalyticsPanel` (global) is untouched.

- [ ] **Step 1: Update the test (failing)**

Render `AdminInsightsPage` inside a seeded `RepositoryContext` (use `renderWithRepo` from `../test/renderWithRepo`, or wrap manually). Drop the `listAllRepositories` mock that fed the page's repo list. Keep knowledge-gap and analytics assertions; the gap panel should now query using the context repo (`r-1`).

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test -- src/pages/AdminInsightsPage.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Convert**

- In `AdminInsightsPage`: remove the `repos`/`reposError`/`listAllRepositories` effect and the `repos` prop threading. Render `<KnowledgeGapsPanel />` and `<AnalyticsPanel />` directly. Keep a loading/error guard only if the context still exposes them (use `useCurrentRepository()`'s `loading`/`error` for the gap section).
- In `KnowledgeGapsPanel`: drop the `{ repos }` prop and its local `selected`/repo `<select>`; add `const { currentRepoId } = useCurrentRepository();` and key its effects on `currentRepoId`. If `currentRepoId === ""`, render the existing empty/no-repo message.

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npm run test -- src/pages/AdminInsightsPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/AdminInsightsPage.tsx frontend/src/pages/AdminInsightsPage.test.tsx
git commit -m "refactor(fe): Insights knowledge-gaps follows shared repository"
```

---

### Task 8: Full suite, docs, and cleanup pass

**Files:**
- Modify: `docs/HANDOFF.md` (per the repo rule: update docs before the PR)
- Verify only: no dead CSS/imports remain

- [ ] **Step 1: Grep for stragglers**

```bash
cd frontend
grep -rn "app-header\|app-brand\|app-nav\b\|AdminSourcesPage\|AdminDatabasePage" src || echo "clean"
```
Expected: `clean` (or only historical references in comments). Remove any live ones.

- [ ] **Step 2: Run the entire frontend gate**

Run: `cd frontend && npm run lint && npm run format:check && npm run typecheck && npm run test && npm run build`
Expected: all green. Fix anything that fails before proceeding.

- [ ] **Step 3: Update HANDOFF.md**

Add a "Done recently" note: the sidebar redesign + unified Data page merged; repository is now a shared context; `/admin/sources` and `/admin/database` are redirects; label "Data" is a one-string i18n change; e2e specs that navigated via the old header need selector updates (CI does not run e2e — follow-up, not a blocker).

- [ ] **Step 4: Commit**

```bash
git add docs/HANDOFF.md
git commit -m "docs: HANDOFF — sidebar redesign + unified Data page"
```

---

## Self-Review

- **Spec coverage:** sidebar + groups (Tasks 2–3) · shared repo switcher/context (Task 1, consumed 4/5/6a/7) · Sources+Database merge with tabs + redirects (6a/6b) · Providers/Repositories/Users untouched (unchanged routes) · EN+UK strings (each task) · behavior tests retargeted (4/5/6a/7) · frontend-only (no backend files touched). Covered.
- **Type consistency:** `RepositoryContextValue` defined once (Task 1) and consumed with the same shape everywhere; `repos: Repository[]` accepts `AdminRepository[]`; `currentRepoId: string` ("" = none) used uniformly; `useCurrentRepository()` name stable across tasks.
- **Placeholder scan:** every code step has complete code; the two large extractions (6a) give the full SourcesPanel and a precise, line-level recipe for DatabasePanel (identical idiom) rather than a vague "similar to".
- **Ordering:** context (1) → sidebar (2) → shell+provider (3) → consumers (4,5) → data (6a,6b) → insights (7) → gate+docs (8). Each ends green and the app stays runnable.
