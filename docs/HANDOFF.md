# ContextVault — Session Handoff

- **Last updated:** 2026-07-23 20:11 EEST (persisted conversations + gap rejection merged #114; fixes PR #115 open)
- **Updated by:** Claude (Opus 4.8) with GoodGlor
- **Board (source of truth for *what to do*):** GitHub Projects "ContextVault" (`GoodGlor`, project #1). Cards = issues in `GoodGlor/ContextVault`. *(This session's work shipped outside the board via superpowers SDD.)*

---

## TL;DR

ContextVault is a full-stack, admin-curated RAG assistant (FastAPI + Postgres/pgvector
backend, React/Vite SPA), feature-complete.

**Merged this session — Persisted conversations + admin knowledge-gap rejection — #114 (squash
`3ec75c6`).** Two features:
- **Persisted conversations.** The query-page chat is now saved server-side per `(user, repo)`
  in new `conversations` + `conversation_turns` tables (each turn stores the answer plus JSONB
  snapshots of its citations/sources). `POST /query` loads history **from the DB**
  (server-authoritative — the client no longer sends `history`) and appends each turn. New
  `GET`/`DELETE /repositories/{id}/conversation` restore/clear the thread; the query page
  hydrates on load and has a **Clear conversation** button.
- **Admin gap rejection.** New `gap_rejections` table; an admin can **reject** a knowledge gap
  with a **required reason** (`POST .../knowledge-gaps/reject`); rejected questions drop out of
  the active gap list; `GET .../knowledge-gaps/rejected` lists them. Admin UI: Reject button +
  reason + a Rejected-gaps section.
- **Bug fixed along the way:** reloading with a valid session logged the user out (`AuthProvider`
  wired the API client in a `useEffect`, so a child's mount-time request raced ahead of it →
  unauthenticated → 401 → session cleared). This silently broke reload-restore; fixed by wiring
  the client synchronously. Verified: backend **389✓**, frontend **74✓**, CI green on #114.

**In flight — PR #115 (`fix/admin-note-title-and-creator-grant`), CI running at handoff.** Two
fixes found while the owner tested the running app:
1. **Admin-note grounding.** A note answering a gap is *titled* with the question and holds only
   the answer in `content`; ingestion embedded only the content, so a terse note (title "яка
   погода в києві", content "10 градусів") was chunked as a bare "10 градусів" — retrieval found
   it (top_score 0.71) but the LLM couldn't ground it and refused. Fix: ingest `title\n\ncontent`.
2. **Auto-grant repo creator.** `POST /repositories` discarded the creating admin; now grants
   them access on creation. (TDD caught a real bug: `repo.id` is `None` until flush — the UUID PK
   default is applied on flush, not at construction — so the first version inserted a NULL
   `repository_id`; fixed with a `flush` before granting.) Verified locally: backend **391✓**,
   ruff/format/mypy clean; RED confirmed before each fix.

**⚠️ Owner action — rotate exposed secrets.** A screenshot shared this session exposed the live
`.env`: `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, and `ENCRYPTION_KEY`. Rotate all three.
Rotating `ENCRYPTION_KEY` invalidates the provider keys stored encrypted in `provider_settings`
— re-enter them in the Providers tab afterward.

**Owner note (from #112, still applies to existing data):** old bge-m3 vectors are incompatible
with Gemini's embedding space — `TRUNCATE chunks;` + re-ingest before trusting retrieval on a
pre-Gemini DB, and set a verified Gemini provider key or every ingest/query 409s.

---

## Repo & branch state

| | Value |
|---|---|
| Current branch | `fix/admin-note-title-and-creator-grant` (pushed; **PR #115 open**, CI running at handoff) |
| `main` HEAD | `3ec75c6` (#114, persisted conversations + gap rejection) |
| In flight | **PR #115** — admin-note grounding + auto-grant creator (2 fixes, verified locally 391✓) |
| Parked | `wip/passage-toggle` (off `main`) — the prior session's passage view/hide toggle, **not reviewed/merged**; also carries stale HANDOFF edits. Decide its fate separately. |
| CI | green on #114; #115 frontend ✓, backend pending at handoff |
| Local infra | `contextvault-db` (pgvector pg16) up + migrated (head `f333a95e2154`) |

**Migrations added this session** (all chain linearly off `d4f1a2b7c9e0`): `f170138d3652`
(conversations + conversation_turns), `f333a95e2154` (gap_rejections). No new enum types. #115
adds no migration.

---

## Done recently (this session)

### Persisted conversations + admin gap rejection — merged as #114 (squash `3ec75c6`; superpowers SDD, 12 tasks)

Built spec → plan → subagent-driven TDD (per-task review + final whole-branch review on the
strongest model). Specs/plans under `docs/superpowers/`.
- **Part 1 — conversations:** `Conversation`/`ConversationTurn` models + migration
  `f170138d3652`; `services/conversations.py` (get-or-create, append_turn with monotonic
  ordinal, `recent_history` oldest-first tail, clear); `POST /query` server-authoritative
  history + turn persistence (the `history` request field removed); `api/conversations.py`
  GET/DELETE (owner-scoped, active-grant gated); `QueryPage` hydrate-on-load + Clear button.
- **Part 2 — gap rejection:** `GapRejection` model + migration `f333a95e2154`;
  `services/knowledge_gaps.py` gains `reject_gap` (upsert on `(repo, normalized_question)`),
  `list_rejected_gaps`, and an exclusion filter in `list_knowledge_gaps`; `api/knowledge_gaps.py`
  POST-reject (422 empty reason, admin-only) + GET-rejected; admin UI Reject flow + Rejected-gaps
  section; EN/UK i18n.
- **Auth reload-logout fix:** `AuthProvider` now wires `configureApi` synchronously in render
  (ref-guarded once), not in a `useEffect` — child mount-time requests no longer race the wiring.
  Regression test reproduces the child-before-parent effect order.
- **Final-review fixes folded in:** normalization parity (`_normalize_text` now `.strip(" ")` to
  match SQL `btrim`, so a rejected gap with edge tabs/newlines can't reappear); `listRejectedGaps`
  `.catch` handlers; a not_in_vault turn-persistence/replay test.
- Docs updated (`docs/architecture.md`, `README.md`). Backend 389✓, frontend 74✓.

### Admin-note grounding + auto-grant repo creator — PR #115 (open, `fix/admin-note-title-and-creator-grant`)

See TL;DR. Root-caused against the **live DB** (admin notes ingest fine; queries retrieve them at
top_score 0.71 but the LLM refuses a context-free terse answer). Files:
`api/sources.py` (ingest `title\n\ncontent`), `api/repositories.py` (`flush` + `grant_access` to
creator), plus TDD tests in `test_admin_notes_api.py` / `test_admin_repositories_api.py`.

*Older completed work (#105–#112 etc.) demoted to History.*

---

## Next up

1. **Merge PR #115** once its backend CI goes green (frontend already ✓). Owner asked to confirm
   before merge this session; the standing directive is squash-merge after green.
2. **Rotate the three exposed `.env` secrets** (see TL;DR) — owner action.
3. **Re-tune `retrieval_min_score` for Gemini embeddings (worth a card).** With Gemini, even
   loosely-related chunks score ~0.7, so the current `0.3` threshold (tuned for bge-m3) barely
   filters — the LLM does all the relevance work. Flagged since #112; the "weather" confusion
   above is a symptom. Investigate a higher threshold or a relative/margin cutoff.
4. **Decide the fate of `wip/passage-toggle`** — the parked passage view/hide toggle (frontend,
   green locally last session, never reviewed). Rebase onto current `main`, review, PR or drop.
5. **SSRF DNS-rebinding / TOCTOU hardening** (`services/web_source.py`) — still open from #100.
   `getaddrinfo` validates the host but httpx re-resolves at connect; not pinned to the validated
   IP. Safe as-is (admin-only, redirects re-validated); harden + `/security-review` before
   non-admin exposure. Worth a card.

Minor deferred items from #114's reviews (recorded, non-blocking): GET `/conversation` does a
write-on-read (get-or-create + commit) with an unguarded first-touch insert race; a QueryPage
"clears" test asserts without `waitFor`; no cascade-delete tests. See the SDD ledger under
`.superpowers/sdd/progress.md` (git-ignored scratch) for the full list.

---

## Open known issues / gotchas

- **UUID primary keys are populated on *flush*, not at construction.** `UUIDPrimaryKeyMixin` uses
  a column `default=uuid.uuid4`, applied at INSERT. If you need a new row's `id` to reference it
  (e.g. create a Grant for a just-created Repository), `await session.flush()` first — else the FK
  column goes in as NULL. (This bit #115; TDD caught it.)
- **Don't query `db_session` directly right after an API call that triggers background ingestion.**
  The admin-note/upload tests run `run_ingestion` against the test session via
  `get_ingestion_session_factory`; a direct `db_session.execute(select(...))` afterward **hangs**.
  Verify through the API instead (e.g. `GET /repositories/{id}/sources/{id}` returns the stored
  passage), like the other tests in `test_admin_notes_api.py`.
- **Stale `.mypy_cache` produces spurious `attr-defined` errors** on `contextvault.services`
  submodule imports (`from contextvault.services import X`). If mypy flags these, `rm -rf
  .mypy_cache && uv run mypy` — a fresh run is authoritative (CI runs fresh).
- **`f"...".encode("utf-8")` trips ruff UP012** (string-literal encode with a redundant arg) even
  though `variable.encode("utf-8")` does not. Use `.encode()`.
- **Conversation history is server-authoritative** — the `/query` request body no longer accepts
  a `history` field. Any client/test still sending it is ignored (harmless), not an error.
- **Frontend tooling versions aligned deliberately:** vitest **3** with vite **6**. Node 25's
  experimental `localStorage` global is non-functional and shadows jsdom's — the in-memory
  `Storage` in `frontend/src/test/setup.ts` stays.
- **`ENCRYPTION_KEY` required** before persisting/using any provider key (`./dev.sh` auto-generates
  one into `.env`; tests get a per-run key from `conftest`).
- **Forced-change enforcement lives in `get_current_user`** — new blockable authenticated
  endpoints should depend on it.
- DB-backed backend tests **skip** (not fail) when Postgres is unreachable; `docker compose up -d`
  + `uv run alembic upgrade head`.
- **e2e is not run by CI** (no Playwright in `.github/workflows`); run manually against `./dev.sh`.
  The `:8000` port can conflict with the owner's other project — run ContextVault with
  `export BACKEND_PORT=8001 VITE_PROXY_TARGET=http://localhost:8001 && ./dev.sh`.

---

## Working rules & gotchas (project conventions)

- **Verify the FULL CI-parity gate before pushing.** Backend CI runs `ruff check src tests`,
  **`ruff format --check src tests`**, **`mypy`** (bare → includes `tests/`), `alembic upgrade head`,
  `pytest`. Running only `ruff check`/`mypy src` misses format diffs and test-only type errors.
  (`ruff check` ≠ `ruff format --check` — a Task-2 file this session was format-dirty but
  lint-clean; caught before merge.)
- **After committing, confirm `git status --porcelain` is empty** and re-run the gate on the
  committed state before declaring green.
- **Frontend DoD (from `frontend/`):** `npm run lint`, `npm run format:check`, `npm run typecheck`,
  `npm test`, `npm run build`. Node 22. New i18n keys go in **both** `en.json` and `uk.json`.
- **TDD:** RED → GREEN (minimal) → full gate. Update docs in the **same PR** — hard rule.
- **Branch from fresh main:** `git fetch && git checkout main && git pull --ff-only` then
  `git checkout -b <slug>`. PRs are **squash-merged**, so after a merge local `main` may diverge
  from squashed `origin/main` — `git reset --hard origin/main`.
- **Merge policy (owner's standing directive):** open the PR, run the full gate + watch CI green,
  then squash-merge. *(This session the owner asked to confirm before each merge — respect that
  until told otherwise.)*
- Migrations (`migrations/versions/`) are NOT in ruff/mypy scope.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## How to run

```bash
# One command — db + migrations + seeded admin + backend + frontend
./dev.sh
# App: http://localhost:5173 (admin / adminpass123) · API docs: http://localhost:8000/docs
# Port conflict? export BACKEND_PORT=8001 VITE_PROXY_TARGET=http://localhost:8001 && ./dev.sh

# Backend gate (CI parity — note format --check and bare mypy):
docker compose up -d && uv run alembic upgrade head
uv run ruff check src tests && uv run ruff format --check src tests && rm -rf .mypy_cache && uv run mypy && uv run pytest

# Frontend gate + e2e (stack must be up for e2e):
cd frontend && npm install && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
cd frontend && npm run test:e2e   # Playwright, against ./dev.sh — NOT in CI
```

See `README.md` for quick start and `docs/architecture.md` for the subsystem/endpoint reference.

---

## History

- **This session:** #114 persisted conversations + admin gap rejection (superpowers SDD, 12
  tasks; includes the auth reload-logout fix) — detailed under *Done recently*. PR #115 (admin-note
  grounding + auto-grant creator) in flight.
- **#112** Gemini API embeddings replace the local torch/bge-m3 embedder (removed
  `sentence-transformers`+`torch`; `GeminiEmbeddingProvider`, 1024-dim asymmetric task; verified
  Gemini key now required, 409 otherwise). Motivated by a torch/MPS SIGSEGV that rebooted the
  owner's Mac; also fixed a bulk-upload DB pool-exhaustion bug. Existing data needs
  `TRUNCATE chunks` + re-ingest.
- **#111** Global provider keys (`ProviderSetting` + migration `d4f1a2b7c9e0`, drops per-repo key)
  + LLM-vision OCR (`llm/ocr.py`, replaces RapidOCR; fixes Cyrillic images).
- **#109** LLM config panel redesign (single model dropdown, optional key). **#108** chat e2e.
  **#107** chat with memory (client-held, since made server-authoritative by #114). **#106**
  multi-file upload. **#105** visible model dropdown + green CI + drop dead `*_api_key` fallbacks.
- **#100–#104** image(OCR)/web sources, HEIC support, dynamic model-list endpoint, EN/UK i18n,
  copy-invite-link. Earlier: admin UI epic (#37–#40), frontend foundation (#34–#36), backend core
  (auth, ingestion, retrieval, providers, citations, not_in_vault, invitations, grants, query
  logging, knowledge gaps, analytics, Admin Notes). See `git log` and the board.
