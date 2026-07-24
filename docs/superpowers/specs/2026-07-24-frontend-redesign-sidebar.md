# Frontend Redesign — Workspace Sidebar + Unified Data — Design Spec

**Date:** 2026-07-24
**Status:** Draft for owner review
**Feature:** Replace the flat top-header navigation with a grouped left **sidebar**
built around a single **repository switcher**, and merge the `Sources` and
`Database` admin pages into one tabbed **Data** section. Frontend-only; no
backend or API changes.

---

## 1. Problem & goal

The current header puts up to **eight** flat links in one row (Ask, Reports,
Repositories, Providers, Sources, Database, Users, Insights) with no grouping and
no separation between *using* the assistant and *administering* it. The
repository — the object everything hangs off — is a small dropdown buried inside
each page, and per-repository config (Sources, Database) looks identical to
global config (Providers, Users). A first-time member faces a wall of options.

**Goal:** an everyday user (member) sees a calm two-item surface; admins get
grouped depth that makes per-repo vs. org-level scope obvious; the repository is
promoted to a single, always-visible switcher. Approved direction: **Option A
(workspace sidebar)** with **Sources + Database merged into "Data."**

**Non-goals:** no changes to backend endpoints, auth, or data model; no new
features; no change to what any screen *does* — only where it lives and how it's
scoped on the client.

## 2. Decisions locked with the owner

| Decision | Choice |
|---|---|
| Primary navigation | **Left sidebar**, replacing the top header |
| Nav grouping | **Workspace** (Ask, Reports) · **Manage this repo** (Data, Providers, Insights) · **Admin** (Repositories, Users) |
| Repository selection | **One shared "current repository" switcher** at the top of the sidebar; repo-centric pages read it instead of owning a dropdown |
| Merge | **Sources + Database → "Data"** page with two tabs: *Documents & web* and *Database* |
| Not merged | Providers, Insights, Repositories, Users stay distinct (different concerns / org-level) |
| Merged-section label | **"Data"** (trivially changeable to "Knowledge" / "Data sources" — one i18n string) |
| Scope of change | **Frontend only** |
| i18n | All new/changed strings in **EN + UK** from the start |

## 3. The shared repository switcher (the core mechanic)

Today each page does its own `listRepositories()` / `listAllRepositories()` +
local `selected` state + `<select>`. The redesign lifts this into a small React
context so the sidebar switcher is the single source of truth.

**`RepositoryContext`** provides:
- `repos: Repository[] | AdminRepository[]` — the accessible list, chosen by role
  (members: `listRepositories()` granted repos; admins: `listAllRepositories()`).
- `currentRepoId: string | ""` and `setCurrentRepoId(id)`.
- Loading / error state for the list.

**Which pages consume it (repo-scoped):** Ask (`/`), Reports (`/reports`), Data
(`/admin/data`), Insights (`/admin/insights`). These drop their internal repo
dropdown and read `currentRepoId`.

**Which pages ignore it (global / org-level):** Providers (`/admin/providers` —
global provider keys), Repositories (`/admin/repositories`), Users
(`/admin/users`). These are unaffected; they keep managing their own lists.

**Behavior:**
- The switcher defaults to the first accessible repo, mirroring today's
  "default to first" behavior on every page.
- Switching repos updates every repo-scoped page in place. Ask already reloads
  the per-repo saved conversation on `selected` change — that logic moves to
  reading `currentRepoId`, so cross-repo history isolation is preserved.
- `currentRepoId` persists to `localStorage` so a reload keeps the last repo
  (small, additive; falls back to first repo if the stored id is gone).
- A member with access to exactly one repo sees the switcher as a static label
  (no dropdown affordance needed, but it still renders for consistency).

**Users-grants sub-panel** currently has its own repo picker to choose *whose*
grants to edit; that is a different axis (an admin editing any repo's access, not
"the repo I'm working in"), so it keeps its local selector. Documented so the
reviewer doesn't flag it as a missed conversion.

## 4. Navigation & layout

New `Layout` renders a left sidebar + routed content (`<Outlet/>`).

```
┌──────────────┬───────────────────────────────┐
│ ContextVault │  (page header + content)      │
│ ┌──────────┐ │                               │
│ │ Repo ▾   │ │                               │
│ └──────────┘ │                               │
│ WORKSPACE    │                               │
│  💬 Ask      │                               │
│  📊 Reports  │                               │
│ MANAGE ▸repo │  (admins only)                │
│  🧠 Data     │                               │
│  🔌 Providers│                               │
│  📈 Insights │                               │
│ ADMIN        │  (admins only)                │
│  📁 Repos    │                               │
│  👥 Users    │                               │
│ ───────────  │                               │
│  AL artem ▾  │  (language, log out)          │
└──────────────┴───────────────────────────────┘
```

- **Groups** rendered from a config array (`{group, items:[{to,label,icon,adminOnly}]}`)
  so nav is data-driven, not hand-repeated. `Manage this repo` and `Admin`
  groups render only for `session.role === "admin"`.
- **Active state** via `NavLink` (existing pattern).
- **Sidebar footer** holds the user identity, the existing `LanguageSwitcher`,
  and Log out (moved out of the header).
- **Responsive:** below a breakpoint the sidebar collapses to a top bar with a
  hamburger toggle that opens it as an overlay. Everyday users (2 items) never
  feel cramped; the collapse mainly serves admins on narrow screens.

## 5. The unified "Data" page

New `AdminDataPage` at `/admin/data`, replacing `/admin/sources` and
`/admin/database`.

- Two tabs (ARIA `role="tablist"`): **Documents & web** and **Database**.
- Tab content is the **existing** page bodies, extracted into two components with
  no behavior change:
  - `SourcesPanel` ← current `AdminSourcesPage` body (upload, ingestion polling,
    delete). Its "Add source" action stays.
  - `DatabasePanel` ← current `AdminDatabasePage` body (connect, test, introspect,
    allow-list, masked creds). Its "Test connection" action stays.
- Both panels read `currentRepoId` from context (replacing their own dropdowns).
- Active tab reflected in the URL (`/admin/data?tab=database`) so it's linkable
  and survives reload; defaults to `documents`.
- **Redirects:** `/admin/sources` → `/admin/data?tab=documents`,
  `/admin/database` → `/admin/data?tab=database`, so any bookmarks still work.

## 6. Routing changes

| Before | After |
|---|---|
| `/` (Ask) | `/` (Ask) — unchanged route, repo from context |
| `/reports` | `/reports` — repo from context |
| `/admin/sources` | **redirect →** `/admin/data?tab=documents` |
| `/admin/database` | **redirect →** `/admin/data?tab=database` |
| — | `/admin/data` (new, admin-only) |
| `/admin/providers` | unchanged |
| `/admin/insights` | `/admin/insights` — repo from context |
| `/admin/repositories` | unchanged |
| `/admin/users` | unchanged |

## 7. Components & files

- **Create:** `RepositoryContext.tsx` (provider + `useCurrentRepository` hook);
  `Sidebar.tsx` (the nav, driven by a nav-config); `AdminDataPage.tsx`;
  `SourcesPanel.tsx`, `DatabasePanel.tsx` (extracted bodies).
- **Modify:** `Layout.tsx` (sidebar shell instead of header); `App.tsx` (routes +
  redirects + wrap protected area in `RepositoryProvider`); `QueryPage.tsx`,
  `ReportsPage.tsx`, `AdminInsightsPage.tsx` (consume context, drop dropdown);
  `index.css` (sidebar styles; retire header-only styles); `en.json` + `uk.json`
  (new nav labels: `nav.data`, group labels `nav.groupWorkspace`,
  `nav.groupManage`, `nav.groupAdmin`, `data.tabDocuments`, `data.tabDatabase`).
- **Delete:** `AdminSourcesPage.tsx`, `AdminDatabasePage.tsx` (bodies live on in
  the panels; keep their `.test.tsx` intent by moving/retargeting tests to the
  panels).

## 8. Testing strategy

- **Keep behavior tests green:** the extracted `SourcesPanel` / `DatabasePanel`
  reuse the existing `AdminSourcesPage.test` / `AdminDatabasePage.test`
  assertions, retargeted to the panels (render inside a `RepositoryProvider` with
  a stub `currentRepoId`).
- **New tests:**
  - `RepositoryContext`: default-to-first, switch updates value, `localStorage`
    persistence + stale-id fallback, role-based list source.
  - `Sidebar`: member sees only Workspace group; admin sees all three groups;
    active link; groups hidden for non-admin.
  - `AdminDataPage`: renders both tabs; tab switch swaps panels; `?tab=` drives
    initial tab; redirects from `/admin/sources` and `/admin/database`.
  - `QueryPage` / `ReportsPage`: consume `currentRepoId`; switching repo in
    context reloads the page's data (conversation isolation preserved for Ask).
- **Full suite** (`npm run test`, lint, typecheck, build) green before the PR.

## 9. Rollout / risk

- Frontend-only; no migration, no API contract change → low blast radius.
- Biggest risk is regressing per-page repo behavior during the dropdown→context
  lift; mitigated by retargeting the existing behavior tests and doing the lift
  one page per task.
- e2e specs that navigate via header links (if any) will need selector updates;
  CI does not run e2e, so this is a follow-up note, not a CI blocker.

## 10. Alternatives considered

- **Keep per-page dropdowns, add a decorative sidebar switcher** — rejected: the
  switcher would be a lie; two sources of truth for "current repo" invites drift.
- **Option B (Settings drawer) / Option C (focused home)** — considered in the
  interface study; owner chose A. C's example-prompt home remains a possible
  later enhancement to the Ask screen, out of scope here.
- **Fold Providers + Insights under a "Settings" item** — deferred: they're used
  at different moments (setup vs. monitoring) and don't share a screen the way
  Sources and Database do.

## 11. Out of scope

Example-prompt Ask home (Option C); repository-in-URL routing
(`/r/:repoId/...`); collapsing Providers/Insights; any backend change; DOCX/PPTX
or other report work.
