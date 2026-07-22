# ContextVault — Session Handoff

- **Last updated:** 2026-07-22 (HEIC support)
- **Updated by:** Claude (Opus 4.8) with GoodGlor
- **Board (source of truth for *what to do*):** GitHub Projects "ContextVault" (`GoodGlor`, project #1). Cards = issues in `GoodGlor/ContextVault`.

---

## TL;DR

ContextVault is a full-stack, admin-curated RAG assistant (FastAPI + Postgres/pgvector
backend, React/Vite SPA), feature-complete. The prior session shipped **image (OCR) &
web-link sources** (PR #100, `2934091`). **This session added `.heic`/`.heif` (iPhone)
image support** — a small extension of the image pipeline: `pillow-heif` decodes HEIC
into a PIL image, then the existing OCR → chunk → embed → cite path runs unchanged.
Spec: `docs/superpowers/specs/2026-07-22-heic-image-support-design.md`.

This HEIC work is **feature A of a three-feature user request**; **B and C are queued,
not started** (see *Next up*): **B** — dynamic LLM model dropdown (enter provider API
key → fetch that provider's available models → select; providers: OpenAI, Anthropic,
Google); **C** — EN/UK i18n with **Ukrainian as default**. Each gets its own spec cycle.

Also open (from #100, not carded): DNS-rebinding hardening of the URL fetcher — safe as-is
(admin-only), but get a `/security-review` before any non-admin exposure. See *Next up*.

---

## Repo & branch state

| | Value |
|---|---|
| Current branch | `main` (synced with origin, clean) |
| `main` HEAD | HEIC support (`#101`), squash-merged this session; before it `2934091` (#100) |
| Last merged PR | **`#101`** — HEIC/HEIF image support; before it #100 (image/web sources) |
| In flight | none |

**Clean state.** Working tree clean; `main` even with `origin/main`. The HEIC PR was
**squash-merged**. **Prunable local branches:** `feat/heic-image-support` (merged),
`feat/image-web-sources`, and the old `feat/1-project-scaffolding` (all safe to
`git branch -D`).

---

## Done recently (this session)

### HEIC/HEIF image support — `#101`, squash-merged

Admins can now upload `.heic`/`.heif` (iPhone) images as sources. Minimal extension of
the existing image path — only *decoding* is new; OCR/ingestion/DB `image` kind/citations
are unchanged, **no migration**.

- **Decode:** added `pillow-heif` (`register_heif_opener()` at the top of
  `src/contextvault/ingestion/parsing.py`); the existing `Image.open` in `_parse_image`
  then handles HEIC transparently.
- **Allowlist:** added `.heic`/`.heif` to `IMAGE_SUFFIXES` (single source of truth →
  drives both parser routing and the API `SourceKind.IMAGE` classification). Frontend
  `accept` attribute extended (`AdminSourcesPage.tsx`).
- **Error contract unchanged:** a text-less HEIC OCRs to nothing → `FAILED` with
  `"No text found in image."`; a corrupt HEIC → `"Could not read image file."`.
- **mypy:** `pillow_heif.*` added to the ignore-missing-imports override.
- **Tests:** backend 322 passed / 1 skipped (new `test_parsing.py` HEIC/HEIF cases +
  `test_sources_api.py` HEIC kind); frontend 56 vitest (accept-attribute assertion);
  **e2e** `frontend/e2e/sources.spec.ts` extended with a blank-HEIC upload that reaches
  the no-text failure (proving decode end-to-end). Ran live against the full stack on
  alt ports 8001/5174 (ports 8000/5173 were occupied by an unrelated project). All CI
  checks pass (`ruff format --check`, `ruff check src tests`, `mypy` no-args).
- **Incidental:** `frontend/vite.config.ts` proxy target is now `VITE_PROXY_TARGET`-
  overridable (defaults to `:8000`), so the frontend can point at a backend on a
  non-default port. Backward-compatible.



### Image (OCR) & web-link sources — PR #100 (`2934091`), merged

Two new ingestible **source kinds**, both feeding the existing
parse→chunk→embed→store→cite pipeline unchanged (they only produce extracted text).
Built via superpowers (spec → plan → 8 TDD tasks, each subagent-implemented +
task-reviewed, then a whole-branch review). Design docs:
`docs/superpowers/specs/2026-07-22-image-and-web-sources-design.md` and
`docs/superpowers/plans/2026-07-22-image-and-web-sources.md`.

- **Data model:** `SourceKind` gained `IMAGE`/`WEB`; `Source` gained a nullable
  `source_url`; Alembic migration `a1b2c3d4e5f6` (Postgres `ALTER TYPE … ADD VALUE`
  outside a txn, idempotent) + the column.
- **Image sources:** reuse the existing `POST /repositories/{id}/sources` upload; image
  suffixes (`.png .jpg .jpeg .webp .tiff .bmp`, shared `IMAGE_SUFFIXES`/`file_suffix`)
  are tagged `kind=image` and OCR'd locally via **`rapidocr-onnxruntime`** behind an
  injectable `ocr_image` (vendor isolated; tests mock it). **Text-only contract:** empty
  OCR → source `FAILED` with `"No text found in image."`.
- **Web-link sources:** new `POST /repositories/{id}/web-sources` (`{ "url": ... }`,
  `AnyHttpUrl` → 422; 404 unknown repo). `run_web_ingestion` (mirrors `run_ingestion`)
  fetches + extracts **off the event loop** and stores via a shared `store_parsed`
  helper. Extraction via **`trafilatura`**. Empty text → `"No readable text found at URL."`.
- **SSRF guard (`services/web_source.py`):** http(s)-only, per-hop host re-validation
  (rejects loopback/private/link-local/reserved/multicast/unspecified — every resolved
  address), streamed 5 MiB cap, 5-hop redirect limit, 15 s timeout, non-HTML rejection,
  `trust_env=False` (proxy can't bypass the resolver check).
- **Frontend:** OCR helper note, image types in the file picker, "Web link" form,
  per-kind source badges; web rows link to `source_url`.
- **New deps:** `rapidocr-onnxruntime`, `pillow`, `trafilatura` (all pure-pip).
- **Tests:** backend 319 passed / 1 skipped (OCR + network mocked → offline/deterministic);
  frontend 55 vitest; **new Playwright e2e** `frontend/e2e/sources.spec.ts` — ran live
  against the full stack (web-link row + badge/link; text-less image → `image` badge →
  `FAILED` with the no-text message), both e2e specs green.

**CI post-merge fixes (folded into the same PR before merge):** CI runs
`ruff format --check src tests` and `mypy` with **no args** (checks `tests/` too). Running
only `ruff check` / `mypy src` locally missed a format diff and test-only type errors;
also a partial `git add` twice left a verified fix uncommitted → red CI. See the new
gotcha under *Working rules*.

---

## Next up

**Two user-requested features are queued (not carded), to be done one at a time via the
superpowers spec→plan→TDD flow:**

- **B — Dynamic LLM model dropdown.** After an admin enters a provider API key, fetch that
  provider's list of available models and let them pick one from a dropdown (rather than a
  free-text/hardcoded model id). **Providers to support: OpenAI, Anthropic, Google
  (Gemini)** — all three SDKs are already deps. Touches LLM/provider config (where keys are
  stored encrypted), a new per-provider "list models" call, and the admin config UI. The
  user referenced the target select element `id="model-b9228a20-3fea-4900-86fa-2d609ea22aa7"`
  — locate the current model-config UI first. Needs its own brainstorm/spec.
- **C — i18n (English ⇄ Ukrainian, Ukrainian default).** App-wide frontend change: a
  translation framework, extract all UI strings, a language switch, persisted preference,
  **default = Ukrainian**. Broad (touches most components); its own spec.

The concrete follow-up surfaced by the #100 code review:

- **SSRF DNS-rebinding / TOCTOU hardening** (`src/contextvault/services/web_source.py`).
  The guard resolves the host with `getaddrinfo`, but httpx **re-resolves at connect**, so
  the connection isn't pinned to the validated IP — attacker-controlled DNS with a short
  TTL could point a validated host at `127.0.0.1`/`169.254.169.254` on the second lookup.
  **Acceptable to ship now:** the feature is admin-only and every redirect hop is
  re-validated. **Before non-admin exposure:** harden (resolve once, then connect to the
  validated literal IP with the original host as SNI/Host, e.g. a custom httpx transport)
  and run `/security-review` on the fetch path. Worth a card.

Other minor follow-ups (nice-to-have, not blocking):
- Pin `requires-python` ≥ 3.12.4 (or normalize `ipv4_mapped`) so IPv4-mapped IPv6
  loopback/metadata addresses can't slip the SSRF classifier on older CPython patches.
- Content-type filter accepts any `text/*`, not just HTML (currently harmless — trafilatura
  degrades gracefully).

Older candidate work (still not carded): token refresh/session renewal; char-span-scoped
citation passages; admin repo-list search/pagination. Create a card before starting any.

---

## Open known issues / gotchas

- **Frontend tooling versions are aligned deliberately:** vitest **3** with vite **6**
  (vitest 2 pulls a nested vite 5 → a dual-vite type clash). Keep them in step on upgrades.
- **Node 25's experimental `localStorage` global is non-functional and shadows jsdom's.**
  The test setup (`frontend/src/test/setup.ts`) installs an in-memory `Storage`; keep it.
- **`ENCRYPTION_KEY` required** before persisting or using any provider key. Generate:
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  (`./dev.sh` auto-generates one into `.env` on first run.) Tests get a per-run key from `conftest`.
- **Forced-change enforcement lives in `get_current_user`** — any new authenticated backend
  endpoint that must be blockable by the bounce should depend on it.
- **CI warning (cosmetic):** `astral-sh/setup-uv@v6` runs on the deprecated Node 20.
- DB-backed backend tests **skip** (not fail) when Postgres is unreachable; bring it up with
  `docker compose up -d` + `uv run alembic upgrade head`. (The persistent "1 skipped".)
- **OCR/web ingestion is mocked in tests** — the suite never runs RapidOCR or hits the
  network. To exercise them for real, use the e2e spec against `./dev.sh` (below).

---

## Working rules & gotchas (project conventions)

- **Board discipline:** cards are issues 1:1. Backlog/Ready → In progress at start,
  → In review when the PR opens, → Done after merge. Assign issues/PRs `--assignee @me`.
  PRs reference cards with `Refs #N`. Tick checkboxes **honestly**. (Use `work-on-card`.)
  *(This session's feature shipped outside the board via superpowers; the DNS-rebinding
  follow-up should be carded.)*
- **Verify the FULL CI-parity gate before pushing — CI checks more than the obvious.**
  Backend CI runs `ruff check src tests`, **`ruff format --check src tests`**, **`mypy`
  (no args → includes `tests/`)**, `alembic upgrade head`, `pytest`. Running only
  `ruff check` or `mypy src` locally **will miss** format diffs and test-only type errors.
- **`git add -p`/partial staging bit twice this session:** a verified fix stayed
  uncommitted and CI kept failing on the old file. **After committing, run
  `git status --porcelain` and confirm it's empty**, and re-run the gate on the committed
  state, before declaring green.
- **Backend DoD (all green):** the five CI steps above.
- **Frontend DoD (all green, from `frontend/`):** `npm run lint`, `npm run format:check`,
  `npm run typecheck`, `npm test`, `npm run build`. Node 22.
- **E2e (Playwright):** `frontend/e2e/*.spec.ts` drive the **real running stack** — bring
  it up with `./dev.sh` first, then `cd frontend && npm run test:e2e`. Not part of the CI
  jobs; run manually.
- **TDD:** RED → GREEN (minimal) → full gate. Update docs (README / `docs/`) in the **same
  PR** — hard rule. **No** "Implementation status" checklist in the README.
- **Branch from fresh main:** `git fetch && git checkout main && git pull --ff-only` then
  `git checkout -b feat/<slug>`. Note: PRs are **squash-merged**, so after a merge your
  local `main` may diverge from a squashed `origin/main` — `git reset --hard origin/main`.
- **Merge policy (owner's standing directive):** open the PR, run the full gate + watch CI
  green, then squash-merge to `main` and move the card to Done autonomously.
- Migrations (`migrations/versions/`) are NOT in ruff/mypy scope. Postgres enum value
  additions use `ALTER TYPE … ADD VALUE IF NOT EXISTS` **outside** the txn (`op.execute("COMMIT")`).
- Commit trailer: `Co-authored-by: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## How to run

```bash
# One command — db + migrations + seeded admin + backend + frontend
./dev.sh
# App: http://localhost:5173 (admin / adminpass123) · API docs: http://localhost:8000/docs

# Backend gate (CI parity — note format --check and bare mypy):
docker compose up -d && uv run alembic upgrade head
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run pytest

# Frontend gate + e2e (stack must be up for e2e):
cd frontend && npm install && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
cd frontend && npm run test:e2e   # Playwright, against ./dev.sh
```

See `README.md` for a quick start and `docs/architecture.md` for the full subsystem/endpoint reference.

---

## History

- **#100 Image (OCR) & web-link sources** — squash `2934091` (14 commits; spec+plan under
  `docs/superpowers/`). Local OCR (RapidOCR), SSRF-guarded web fetch (trafilatura), shared
  `store_parsed`, Playwright e2e. *(this session — built via superpowers, not a board card)*
- #98 Visual polish + Playwright e2e — PR #99. #97 handoff refresh.
- #91 Deflake expired-grant test — PR #96. #90 User-facing source content — PR #95. #89 Repo rename/delete — PR #94.
- Docs: neat README + `docs/architecture.md` split — PR #93; Contributing/License — #92; overview/TOC — #88. `dev.sh` — #83.
- Admin UI epic: #40 Insights — PR #87. #39 Users & grants — PR #86. #38 Sources — PR #85. #37 Repositories — PR #84.
- Frontend foundation: #36 Query UI — PR #81. #35 Auth UI — PR #80. #34 Scaffolding — PR #79.
- Backend: FastAPI + pgvector + Argon2/JWT auth + admin bootstrap, ingestion pipeline, local embeddings, access-filtered retrieval, providers (Gemini/OpenAI/OpenRouter/Anthropic), numbered-chunk citations, `not_in_vault`, per-repo LLM config, encrypted keys, invitations, grants, query logging, knowledge gaps, analytics, Admin Notes. See `git log` and the board.
