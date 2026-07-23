# ContextVault — Session Handoff

- **Last updated:** 2026-07-23 (multi-file upload; chat+memory in flight)
- **Updated by:** Claude (Opus 4.8) with GoodGlor
- **Board (source of truth for *what to do*):** GitHub Projects "ContextVault" (`GoodGlor`, project #1). Cards = issues in `GoodGlor/ContextVault`.

---

## TL;DR

ContextVault is a full-stack, admin-curated RAG assistant (FastAPI + Postgres/pgvector
backend, React/Vite SPA), feature-complete. The **three-feature user request is now fully
shipped**, one PR each: **A** — HEIC/HEIF image support (`#101`); **B** — dynamic LLM
model dropdown (`#102`); **C** — EN/UK i18n (`#103`), **shipped this session**.

**Feature C (`#103`):** the whole SPA is bilingual (English + Ukrainian) via
**react-i18next**, with **Ukrainian as the default** and a language switcher in the header
and on the auth cards. All ~150 user-facing strings extracted to `src/i18n/locales/{en,uk}.json`;
choice persists in `localStorage` (`contextvault.locale`). Spec:
`docs/superpowers/specs/2026-07-22-i18n-uk-design.md`. **No backend changes.**

**Follow-up fix (this session):** the model dropdown loaded models into an invisible
`<datalist>` — a successful fetch looked like "nothing happened" (reported against Gemini;
the backend was always correct — verified live: 56 models, 41 with `generateContent`). Now
a real `<select>` dropdown + a "Loaded N models" confirmation. Same PR: **CI is green again**
(was red since #101 — prettier on 3 files, plus a masked `process`/`vite.config.ts` typecheck
error → added `@types/node`), and the dead process-wide `*_api_key` settings/fallbacks were
removed (per-repo encrypted keys make them unreachable). See *Done recently*.

Also open (from #100, not carded): DNS-rebinding hardening of the URL fetcher — safe as-is
(admin-only), but get a `/security-review` before any non-admin exposure. See *Next up*.

---

## Repo & branch state

| | Value |
|---|---|
| Current branch | `main` (synced with origin, clean) |
| `main` HEAD | Multi-file upload, squash-merged; before it model-picker/green-CI/env cleanup (#105), #104, #103 |
| Last merged PR | multi-file upload; before it #105 (model-picker/CI/env), #104 (copy invite-link), #103 (i18n) |
| In flight | **chat + memory** on the query page (frontend + backend) — see *Done recently* |
| CI | **green** (was red #101–#104: prettier + a masked `vite.config.ts` typecheck error) |

**Clean state.** Working tree clean; `main` even with `origin/main`. The invite-copy PR
was **squash-merged**. **Prunable local branches:** `feat/copy-invite-link` (merged),
`feat/i18n-uk`, `feat/model-dropdown`, `feat/heic-image-support`, `feat/image-web-sources`,
and the old `feat/1-project-scaffolding` (all safe to `git branch -D`).

---

## Done recently (this session)

### Multi-file upload on the admin Sources page — squash-merged

The document picker took one file at a time. Now `<input multiple>` + upload every
selected file concurrently via `Promise.allSettled` (one failure doesn't sink the rest;
successes append, failures summarised). Each file already becomes its own background-ingested
source, so **no backend change**. Labels/button reflect the count ("Upload N files"); EN + UK
strings added; e2e `sources.spec.ts` label updated ("Document" → "Documents"). Frontend only.

### Chat + memory on the query page — IN FLIGHT (next)

User asked for the query UX to "look like a chat conversation, not Q&A", and chose the
**chat + memory** scope: a real chat UI (user/assistant bubbles, bottom composer, auto-scroll)
**plus** conversational memory so follow-ups carry context. Backend: `QueryRequest` gains an
optional bounded `history`; `LLMProvider.answer` + shared `build_user_message` render a
"Conversation so far" preamble; `SYSTEM_PROMPT` gains a line (use history only to interpret the
question — still answer ONLY from numbered sources, prior answers are context not sources);
retrieval contextualised by prepending the previous question for embedding only. Frontend:
redesign `QueryPage` into chat, send running `history`, fresh conversation on repo change.

### Model-picker UX + green CI + drop dead provider-key env fallbacks — squash-merged

Three related fixes in one PR (branch `fix/model-picker-ux-and-ci`):

- **Model dropdown made visible (the "Gemini does not work" report).** Root cause was
  *not* Gemini: the list-models backend is correct — verified against the live Gemini API
  (56 models, 41 with `generateContent`). The frontend pushed results into a `<datalist>`,
  which renders **no visible change**, so a successful load looked like nothing happened.
  Replaced with a real `<select>` dropdown that appears once models load, plus a
  "Loaded N models" confirmation; selecting one fills the still-free-text Model input.
  New i18n keys `repositories.chooseModel` / `chooseModelPlaceholder` / `modelsLoaded`
  (EN + UK). `AdminRepositoriesPage.test.tsx` updated to assert the `<select>` + pick-fills-input.
- **CI green again (red since #101).** Prettier flagged 3 unformatted files
  (`AdminRepositoriesPage.tsx/.test.tsx`, `AdminUsersPage.tsx`) — `npm run format`. And
  masked behind that early failure, `tsc` couldn't find `process` in `vite.config.ts`
  (the `VITE_PROXY_TARGET` override) → added `@types/node` + `"types":["node"]` in
  `tsconfig.node.json`. Full suite now passes (ruff/mypy/pytest 334✓, vitest 60✓, build, e2e 2✓).
- **Removed dead process-wide provider keys.** `build_repo_llm` always passes the repo's
  own decrypted key ("never a process-wide default"), so the four `*_api_key` settings and
  their `or settings.X_api_key` fallbacks were unreachable. Dropped the config fields, the
  provider fallbacks, and the `.env.example` entries. Local `.env` left untouched (gitignored;
  `extra="ignore"` means leftover key lines are harmless — safe to delete by hand).

### Copy invite-link button — `#104`, squash-merged

Small UX add on the admin Users page. After creating an invite, a **Copy** button writes
the full accept-invite URL (`{origin}/accept-invite?token=…`) to the clipboard and flips
to **Copied** for 2s. The displayed `<code>` now shows that absolute URL too (was a
relative path). Clipboard failures (insecure context / denied permission) are swallowed —
the link stays visible to copy by hand. New i18n keys `users.copyLink` / `users.copiedLink`
(EN + UK). Frontend only. Tests: `AdminUsersPage.test.tsx` (mocked clipboard → asserts the
URL written + the Copied flip); e2e `admin.spec.ts` extended (create invite → Copy →
Copied, clipboard holds the URL, with `clipboard-*` permissions granted).

### i18n — English ⇄ Ukrainian, Ukrainian default — `#103`, squash-merged

The whole SPA is bilingual via **react-i18next**; **Ukrainian is the default**, English
is switchable. **Frontend only — no backend changes.**

- **Setup:** `src/i18n/index.ts` (init, `localStorage` persist under
  `contextvault.locale`, default `uk`, fallback `en`); `main.tsx` imports it once. Deps
  `i18next` + `react-i18next` added.
- **Catalog:** all ~150 user-facing strings → `src/i18n/locales/en.json` + `uk.json`,
  grouped by namespace (`common`, `layout`, `nav`, `login`, `changePassword`,
  `acceptInvite`, `query`, `queryTurn`, `sourceList`, `answerText`, and one per admin
  page). Pluralized counts use i18next `_one/_other` (EN) and `_one/_few/_many/_other`
  (UK). The four admin-page translations were done by parallel subagents against a shared
  glossary, then merged.
- **Switcher:** `components/LanguageSwitcher.tsx` (Українська / English) in the header
  (`Layout`) and on each auth card (login / change-password / accept-invite).
- **Not translated:** dynamic data, API error *detail* strings, CSS classes, ids, the
  upload `accept` attribute. Status **CSS classes** keep the raw enum (`status-failed`);
  only the label is translated.
- **Tests:** `src/test/setup.ts` pins the unit-test locale to English so the existing
  ~150 English-string assertions keep passing; `LanguageSwitcher.test.tsx` verifies the
  flip to Ukrainian. e2e specs `addInitScript` to force English; a throwaway check
  confirmed the default-Ukrainian login renders ("Увійти"). Frontend 59 vitest, eslint
  (max-warnings=0) + tsc clean; both e2e specs green on the live stack (alt ports).

### Dynamic LLM model dropdown — `#102`, squash-merged

After an admin enters a provider API key and clicks **"Load models"**, the app fetches
that provider's live model catalogue and turns the Model field into a dropdown you can
still type into (the "dropdown + manual fallback" the user chose).

- **`llm/models.py` (new):** `async list_models(provider, api_key, base_url=None)` calls
  each SDK's list endpoint — Anthropic/OpenAI `client.models.list()`, Gemini
  `client.aio.models.list()`, OpenRouter via the OpenAI client + `base_url`. Light
  chat-model filtering: OpenAI kept to `gpt-*`/`o<n>`/`chatgpt-*`; Gemini kept to
  `generateContent` models; Anthropic/OpenRouter returned as-is. Any failure → typed
  `ModelListError`.
- **Endpoint:** `POST /repositories/{id}/llm-models` (admin-only), body
  `{provider, api_key?}`. Uses the entered key, else the repo's **stored** encrypted key
  (so a configured repo reloads without re-sending the masked secret); no key → 400;
  `ModelListError` → 400; unknown repo → 404.
- **Frontend (`RepoConfigPanel`):** "Load models" button + a `<datalist>`-backed Model
  `<input>`; provider change clears the stale list; loading/error states inline. New
  `listModels` client fn.
- **Trigger/field/providers** were user decisions: explicit button; dropdown + manual
  fallback; all four providers.
- **Tests:** backend 334 passed / 1 skipped — `tests/test_llm_models.py` (per-provider
  listing + filtering, mocked SDK clients) and `tests/test_repositories_api.py`
  (entered-key vs stored-key, no-key 400, provider-error 400, 403, 404). Frontend 58
  vitest (Load-models populates the datalist; failure shows an error). **e2e**
  `admin.spec.ts` extended: "Load models" with a bad key surfaces a clean error
  end-to-end. Ran live on alt ports 8001/5174. All CI checks pass.

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

**The three-feature request (A #101, B #102, C #103) is fully shipped. No feature work is
queued.** New i18n keys should be added to *both* `src/i18n/locales/en.json` and `uk.json`
(en/uk key sets must match, except UK's extra `_few`/`_many` plural forms); any new
user-facing string must go through `t()`, or it will render only in English.

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
